"""Persistent Moonraker WebSocket listener and Hermes delivery worker.

No G-code or motion control is issued. Printer status and diagnostic queries only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

import aiohttp

from .config import Settings
from .events import Alert, EventProcessor
from .force import OBJECT as FORCE_OBJECT
from .force import ForceEventTracker
from .notifier import deliver
from .reports import PeriodicReporter
from .storage import Outbox

LOG = logging.getLogger("kalico-monitor")
SUBSCRIBE = {"jsonrpc": "2.0", "method": "printer.objects.subscribe",
             "params": {"objects": {"print_stats": None, "webhooks": None,
                                    "virtual_sdcard": None, "extruder": None,
                                    "heater_bed": None}}, "id": 1}
def subscribe_request(include_force: bool = False) -> dict:
    """Optional objects are opt-in; use base subscription on unsupported Klippy."""
    objects = dict(SUBSCRIBE["params"]["objects"])
    if include_force:
        objects[FORCE_OBJECT] = None
    return {**SUBSCRIBE, "params": {"objects": objects}}


QUERY = {"jsonrpc": "2.0", "method": "printer.objects.query",
         "params": {"objects": {"webhooks": None}}, "id": 2}


class Monitor:
    def __init__(self, settings: Settings, store: Outbox):
        self.settings = settings
        self.store = store
        self.last_store_at = 0.0
        self.last_heartbeat_at = float("-inf")
        self.last_status_received_at: float | None = None
        self.reporter = PeriodicReporter(store, settings.progress_report_minutes)
        self.force_tracker = ForceEventTracker(store) if settings.watch_nozzle_force else None
        self.last_stored_state = store.get_state()
        self.klippy_down_since: float | None = None
        self.klippy_down_alerted = False
        self.klippy_down_id = 0
        self.ws_offline_since: float | None = None
        self.ws_offline_notified = False
        self.ws_offline_id = 0
        self.shutdown_query_since: float | None = None

    def _save(self, current: dict, alerts: list[Alert]):
        previous = self.last_stored_state
        important = (previous is None or
                     any(current.get(k) != previous.get(k) for k in
                         ("state", "klippy_state", "generation", "filename", "stops")))
        # Avoid writing an SD/eMMC card every progress notification.
        if alerts or important or time.monotonic() - self.last_store_at >= 15:
            self.store.transition(current, alerts)
            self.last_stored_state = dict(current)
            self.last_store_at = time.monotonic()
            for alert in alerts:
                LOG.warning("queued %s %s", alert.kind, alert.detail)

    def _record(self, processor: EventProcessor, current: dict, alerts: list[Alert]) -> None:
        """Persist optional force events before generic pause notifications."""
        if self.force_tracker is not None:
            self.force_tracker.observe(processor.status, current)
            if self.force_tracker.suppress_generic_pause(current):
                alerts = [a for a in alerts if a.kind != "paused"]
        self._save(current, alerts)

    def _heartbeat(self) -> None:
        if time.monotonic() - self.last_heartbeat_at >= 30:
            self.store.set_meta("last_heartbeat_at", time.time())
            self.last_heartbeat_at = time.monotonic()

    def _new_incident(self, key: str) -> int:
        value = int(self.store.get_meta(key, 0)) + 1
        self.store.set_meta(key, value)
        return value

    def _system_alert(self, kind: str, generation: int, detail: str,
                      severity: str = "critical"):
        event = Alert(kind=kind, generation=generation, filename="", detail=detail,
                      severity=severity)
        self.store.enqueue(event)
        LOG.warning("queued %s %s", kind, detail)

    async def _dispatch(self, session: aiohttp.ClientSession):
        while True:
            for item in self.store.pending(limit=12):
                try:
                    await deliver(session, self.settings.hermes_url,
                                  self.settings.hermes_secret, self.settings.printer, item)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    self.store.failed(item["id"], type(exc).__name__ + ": " + str(exc)[:100])
                    LOG.warning("notification retry scheduled: %s", item["id"])
                else:
                    self.store.delivered(item["id"])
                    LOG.info("notification delivered: %s", item["id"])
            await asyncio.sleep(2)

    def _process_snapshot(self, processor: EventProcessor, snapshot: dict):
        self._heartbeat()
        self.last_status_received_at = time.monotonic()
        if self.ws_offline_notified:
            self._system_alert("moonraker_recovered", self.ws_offline_id,
                               "Moonraker subscription restored", "info")
        self.ws_offline_since = None
        self.ws_offline_notified = False
        state, alerts = processor.set_snapshot(snapshot, time.time())
        self._record(processor, state, alerts)
        hooks = snapshot.get("webhooks", {})
        if isinstance(hooks, dict) and hooks.get("state") == "ready":
            self._klippy_recovered()
        elif isinstance(hooks, dict) and hooks.get("state") in {"disconnected", "startup"}:
            self._klippy_disconnected()
        elif isinstance(hooks, dict) and hooks.get("state") in {"shutdown", "error"}:
            self.klippy_down_since = None
            self.shutdown_query_since = None

    def _klippy_disconnected(self):
        if self.klippy_down_since is None:
            self.klippy_down_since = time.monotonic()

    def _klippy_recovered(self):
        self.klippy_down_since = None
        if self.klippy_down_alerted:
            self._system_alert("klippy_recovered", self.klippy_down_id,
                               "Moonraker reports Kalico ready again; check before restarting print", "info")
            self.klippy_down_alerted = False

    def _tick(self, processor: EventProcessor):
        self._heartbeat()
        # Shutdown notification may have no payload. If query never returns,
        # issue generic shutdown warning rather than suppressing the event.
        if (self.shutdown_query_since is not None and
                time.monotonic() - self.shutdown_query_since >= 3):
            self.shutdown_query_since = None
            delta = {"webhooks": {"state": "shutdown", "state_message":
                        "Kalico Shutdown event received; state query did not return details"}}
            if processor.has_snapshot:
                current, alerts = processor.apply_update(delta, time.time())
            else:
                current, alerts = processor.set_snapshot(delta, time.time())
            self._record(processor, current, alerts)
        if processor.has_snapshot:
            current, alerts = processor.evaluate(time.time())
            self._record(processor, current, alerts)
            connected = (
                self.klippy_down_since is None
                and self.shutdown_query_since is None
                and self.last_status_received_at is not None
                and time.monotonic() - self.last_status_received_at <= 120
            )
            if self.reporter.evaluate(current, processor.status, time.time(), connected=connected):
                LOG.info("periodic progress report queued")
        if (self.klippy_down_since is not None and not self.klippy_down_alerted
                and time.monotonic() - self.klippy_down_since >= self.settings.disconnect_seconds):
            self.klippy_down_id = self._new_incident("klippy_incidents")
            self._system_alert("klippy_disconnected", self.klippy_down_id,
                               "Moonraker has lost its connection to Kalico")
            self.klippy_down_alerted = True

    async def _listen(self, session: aiohttp.ClientSession):
        headers = {"X-Api-Key": self.settings.moonraker_api_key} if self.settings.moonraker_api_key else {}
        processor = EventProcessor(previous=self.store.get_state(),
                                   stall_minutes=self.settings.stall_minutes)
        async with session.ws_connect(self.settings.ws_url, headers=headers,
                                      heartbeat=20) as ws:
            LOG.info("Moonraker connected; subscribing to status")
            include_force = self.settings.watch_nozzle_force
            await ws.send_json(subscribe_request(include_force))
            requested_at = time.monotonic()
            while True:
                if not processor.has_snapshot and time.monotonic() - requested_at > 20:
                    raise TimeoutError("Moonraker subscription reply timed out")
                try:
                    msg = await ws.receive(timeout=5)
                except asyncio.TimeoutError:
                    self._tick(processor)
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
                                aiohttp.WSMsgType.CLOSE):
                    raise ConnectionError("Moonraker WebSocket closed")
                if msg.type != aiohttp.WSMsgType.TEXT:
                    self._tick(processor)
                    continue
                try:
                    packet = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(packet, dict):
                    continue
                reply_id = packet.get("id")
                if reply_id == 1:
                    # A subscription reply is the complete initial snapshot.
                    if "error" in packet:
                        if include_force:
                            LOG.warning("Optional nozzle_force_monitor subscribe rejected; "
                                        "falling back to base status subscription")
                            include_force = False
                            requested_at = time.monotonic()
                            await ws.send_json(subscribe_request(False))
                            continue
                        raise RuntimeError("Moonraker subscription rejected")
                    result = packet.get("result", {})
                    snapshot = result.get("status") if isinstance(result, dict) else None
                    if not isinstance(snapshot, dict):
                        raise RuntimeError("Moonraker subscription returned no status")
                    self._process_snapshot(processor, snapshot)
                    continue
                if reply_id == 2:
                    # A Klippy failure may reject object queries while Moonraker
                    # itself remains reachable. Keep the WebSocket connected.
                    if "error" in packet:
                        LOG.warning("Moonraker webhooks query rejected; keeping connection")
                        continue
                    result = packet.get("result", {})
                    snapshot = result.get("status") if isinstance(result, dict) else None
                    if isinstance(snapshot, dict) and isinstance(snapshot.get("webhooks"), dict):
                        update = {"webhooks": snapshot["webhooks"]}
                        if processor.has_snapshot:
                            current, alerts = processor.apply_update(update, time.time())
                        else:
                            current, alerts = processor.set_snapshot(update, time.time())
                        self._record(processor, current, alerts)
                        hooks_state = snapshot["webhooks"].get("state")
                        if hooks_state in {"shutdown", "error"}:
                            self.shutdown_query_since = None
                            self.klippy_down_since = None
                        elif hooks_state == "ready":
                            self.shutdown_query_since = None
                            self._klippy_recovered()
                        elif hooks_state in {"disconnected", "startup"}:
                            self._klippy_disconnected()
                    continue

                method = packet.get("method")
                if method == "notify_klippy_shutdown":
                    self.last_status_received_at = None
                    self.shutdown_query_since = time.monotonic()
                    await ws.send_json(QUERY)
                elif method == "notify_klippy_disconnected":
                    self.last_status_received_at = None
                    self._klippy_disconnected()
                    await ws.send_json(QUERY)
                elif method == "notify_klippy_ready":
                    self.last_status_received_at = None
                    self._klippy_recovered()
                    include_force = self.settings.watch_nozzle_force
                    requested_at = time.monotonic()
                    await ws.send_json(subscribe_request(include_force))
                else:
                    current, alerts = processor.handle_packet(packet, time.time())
                    if current is not None:
                        if method == "notify_status_update":
                            self.last_status_received_at = time.monotonic()
                        self._record(processor, current, alerts)
                        hooks = processor.status.get("webhooks", {})
                        if hooks.get("state") == "ready":
                            self._klippy_recovered()
                        elif hooks.get("state") in {"disconnected", "startup"}:
                            self._klippy_disconnected()
                self._tick(processor)

    async def run(self):
        headers = {"User-Agent": "hermes-plugin-kalico-monitor/0.3"}
        async with aiohttp.ClientSession(headers=headers, trust_env=False) as session:
            sender = asyncio.create_task(self._dispatch(session))
            try:
                while True:
                    try:
                        await self._listen(session)
                    except asyncio.CancelledError:
                        raise
                    except (aiohttp.ClientError, OSError, TimeoutError,
                            ConnectionError, RuntimeError) as exc:
                        self.reporter.suspend()
                        self.last_status_received_at = None
                        LOG.warning("Moonraker WebSocket problem: %s", type(exc).__name__)
                        if self.ws_offline_since is None:
                            self.ws_offline_since = time.monotonic()
                        if (not self.ws_offline_notified and
                                time.monotonic() - self.ws_offline_since >= self.settings.disconnect_seconds):
                            self.ws_offline_id = self._new_incident("moonraker_incidents")
                            self._system_alert("moonraker_unreachable", self.ws_offline_id,
                                               "Moonraker WebSocket is unavailable")
                            self.ws_offline_notified = True
                        await asyncio.sleep(3)
                    else:
                        await asyncio.sleep(3)
            finally:
                sender.cancel()
                try:
                    await sender
                except asyncio.CancelledError:
                    pass


def main():
    # Local configuration is explicit and never starts the monitor daemon.
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        from .setup import main as config_main
        config_main(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description="Monitor Kalico through Moonraker; notify via Hermes")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING"))
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.load()
    store = Outbox(settings.sqlite_path, settings.printer)
    try:
        asyncio.run(Monitor(settings, store).run())
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":
    main()
