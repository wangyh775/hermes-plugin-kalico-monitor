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
from .filament_events import OBJECT as FILAMENT_OBJECT
from .filament_events import FilamentEventTracker
from .handlers import agent_url
from .native_events import NativeEventAdapter
from .notifier import deliver
from .policy import parse_modes, routes_for
from .reports import PeriodicReporter
from .rule_engine import RuleEngine, numeric_field
from .storage import Outbox

LOG = logging.getLogger("kalico-monitor")
SUBSCRIBE = {"jsonrpc": "2.0", "method": "printer.objects.subscribe",
             "params": {"objects": {"print_stats": None, "webhooks": None,
                                    "virtual_sdcard": None, "extruder": None,
                                    "heater_bed": None}}, "id": 1}
def subscribe_request(include_force: bool = False, *, filament_only: bool = False,
                      legacy_only: bool = False, extra_objects: tuple[str, ...] = ()) -> dict:
    """Only subscribe to filament_force, plus validated custom Moonraker objects.

    The old compatibility keyword arguments are accepted but no longer
    subscribe to the obsolete nozzle_force_monitor object.
    """
    objects = dict(SUBSCRIBE["params"]["objects"])
    if include_force:
        objects[FILAMENT_OBJECT] = ["events"]
    for name in extra_objects:
        objects.setdefault(name, None)
    return {**SUBSCRIBE, "params": {"objects": objects}}


QUERY = {"jsonrpc": "2.0", "method": "printer.objects.query",
         "params": {"objects": {"webhooks": None}}, "id": 2}


class Monitor:
    def __init__(self, settings: Settings, store: Outbox):
        self.settings = settings
        self.event_modes = parse_modes(settings.event_modes)
        # Explicit [events] overrides take precedence over an event rule's mode.
        for rule in settings.event_rules:
            self.event_modes.setdefault(rule.name, rule.mode)
        self.store = store
        self.native_events = NativeEventAdapter(store, self.event_modes)
        cancelled = store.reconcile_policy(self.event_modes)
        if cancelled:
            LOG.warning("suppressed %s pending routes by updated event policy", cancelled)
        self.last_store_at = 0.0
        self.last_heartbeat_at = float("-inf")
        self.last_status_received_at: float | None = None
        self.reporter = PeriodicReporter(store, settings.progress_report_minutes)
        self.filament_tracker = FilamentEventTracker(store) if settings.watch_nozzle_force else None
        self.rule_engine = RuleEngine(store, settings.event_rules)
        self.last_stored_state = store.get_state()
        self.klippy_down_since: float | None = None
        self.klippy_down_alerted = False
        self.klippy_down_id = 0
        self.ws_offline_since: float | None = None
        self.ws_offline_notified = False
        self.ws_offline_id = 0
        self.shutdown_query_since: float | None = None
        self.disconnect_method: str | None = None
        self.subscription_active = False
        self.field_seen: dict[tuple[str, str], float] = {}
        for prefix in ("klippy", "moonraker"):
            incident = store.get_meta(f"{prefix}_open_incident", {})
            if isinstance(incident, dict) and incident.get("id"):
                if prefix == "klippy":
                    self.klippy_down_alerted = True
                    self.klippy_down_id = incident["id"]
                else:
                    self.ws_offline_notified = True
                    self.ws_offline_id = incident["id"]
        self._invalidate_subscription("starting")

    def _invalidate_subscription(self, state="subscribing"):
        self.subscription_active = False
        self.last_status_received_at = None
        self.field_seen.clear()
        self.rule_engine.suspend()
        self.reporter.suspend()
        self.store.set_meta("subscription_health", {"state": state, "missing": []})
        self.reset_stall_clock = True

    def _remember_fields(self, update):
        now = time.monotonic()
        for obj, fields in update.items():
            if isinstance(fields, dict):
                for field in fields:
                    self.field_seen[(obj, field)] = now

    def _capabilities(self, snapshot):
        required = set(self.rule_engine.objects)
        if self.settings.watch_nozzle_force:
            required.add(FILAMENT_OBJECT)
        missing = {name for name in required if not isinstance(snapshot.get(name), dict)}
        if self.settings.watch_nozzle_force and FILAMENT_OBJECT not in missing:
            if not isinstance(snapshot[FILAMENT_OBJECT].get("events"), list):
                missing.add(FILAMENT_OBJECT + ".events")
        for rule in self.settings.event_rules:
            if numeric_field(snapshot, rule.object, rule.field) is None:
                missing.add(f"{rule.object}.{rule.field}")
        missing = sorted(missing)
        old = self.store.get_meta("capability_state", {}).get("missing", [])
        state = {"state": "degraded" if missing else "active",
                 "missing": missing, "objects": sorted(snapshot)}
        self.store.set_meta("subscription_health", state)
        if missing != old:
            seq = self._new_incident("capability_incidents")
            kind = "monitor_degraded" if missing else "monitor_recovered"
            alert = Alert(kind, seq, "", ", ".join(missing) if missing else
                          "Optional monitoring capabilities restored",
                          "warning" if missing else "info",
                          payload={"missing": missing}, trigger="subscription")
            self.store.report_checkpoint("capability_state", state, alert)

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
        """Persist lifecycle events even when a force alarm also caused PAUSE.

        A force alarm and a print-state transition answer different questions:
        suppressing the latter made real pause callbacks disappear.
        """
        if self.subscription_active and self.filament_tracker is not None:
            self.filament_tracker.observe(processor.status, current)
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
                      severity: str = "critical", source_method: str | None = None,
                      trigger: str | None = None):
        event = Alert(kind=kind, generation=generation, filename="", detail=detail,
                      severity=severity, source_method=source_method, trigger=trigger)
        incident_keys = {
            "klippy_disconnected": ("klippy", True),
            "klippy_recovered": ("klippy", False),
            "moonraker_unreachable": ("moonraker", True),
            "moonraker_recovered": ("moonraker", False),
        }
        if kind in incident_keys:
            prefix, opened = incident_keys[kind]
            self.store.report_checkpoint(
                f"{prefix}_open_incident",
                {"id": generation, "opened_at": time.time()} if opened else {}, event)
        else:
            self.store.enqueue(event)
        LOG.warning("queued %s %s", kind, detail)

    async def _dispatch(self, session: aiohttp.ClientSession):
        await self._supervise(*(self._dispatch_route(session, route)
                                for route in ("direct", "agent")))

    @staticmethod
    async def _supervise(*coroutines):
        tasks = [asyncio.create_task(coro) for coro in coroutines]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            raise RuntimeError("Monitor worker exited unexpectedly")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch_route(self, session, route):
        heartbeat_at = float("-inf")
        while True:
            if time.monotonic() - heartbeat_at >= 15:
                self.store.set_meta(f"dispatcher_heartbeat_{route}", time.time())
                heartbeat_at = time.monotonic()
            for item in self.store.unrouted(limit=30):
                routes = routes_for(item["kind"], self.event_modes)
                # Persist both tasks even when the analysis endpoint is absent.
                # The direct alert must never depend on Agent configuration.
                self.store.ensure_routes(item["id"], routes)
                if not routes:
                    LOG.info("notification suppressed: kind=%s id=%s", item["kind"], item["id"])
            for item in self.store.pending_routes(limit=1, route=route):
                if item["kind"] == "print_progress" and time.time() - item["created"] > 1800:
                    self.store.route_suppressed(item["id"], route, "stale progress report")
                    continue
                url = self.settings.hermes_agent_url if route == "agent" else self.settings.hermes_url
                if route == "agent":
                    handler = self.settings.handler_config.for_event(item["kind"])
                    try:
                        url = agent_url(url, handler)
                    except ValueError:
                        self.store.route_failed(item["id"], route, "invalid Handler base URL")
                        continue
                if not url:
                    self.store.route_failed(item["id"], route, "Webhook route is not configured")
                    LOG.warning("notification %s route is not configured: %s", route, item["id"])
                    continue
                try:
                    options = {}
                    if self.settings.handler_config.templates:
                        options["message_templates"] = self.settings.handler_config.templates
                    await deliver(session, url, self.settings.hermes_secret,
                                  self.settings.printer, item, route=route,
                                  message_fields=self.settings.message_fields, **options)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    self.store.route_failed(item["id"], route,
                                            type(exc).__name__ + ": " + str(exc)[:100])
                    LOG.warning("notification retry scheduled: kind=%s id=%s route=%s cause=%s",
                            item["kind"], item["id"], route, type(exc).__name__)
                else:
                    self.store.route_delivered(item["id"], route)
                    LOG.info("notification accepted: kind=%s id=%s route=%s",
                         item["kind"], item["id"], route)
            await asyncio.sleep(0.2)

    def _process_snapshot(self, processor: EventProcessor, snapshot: dict):
        self._heartbeat()
        self.last_status_received_at = time.monotonic()
        self.subscription_active = True
        if self.reset_stall_clock and processor.previous:
            processor.previous = {**processor.previous, "progress_changed_at": time.monotonic()}
        self.reset_stall_clock = False
        self.field_seen.clear()
        self._remember_fields(snapshot)
        self._capabilities(snapshot)
        if self.ws_offline_notified:
            self._system_alert("moonraker_recovered", self.ws_offline_id,
                               "Moonraker subscription restored", "info")
        self.ws_offline_since = None
        self.ws_offline_notified = False
        state, alerts = processor.set_snapshot(snapshot, time.monotonic())
        self._record(processor, state, alerts)
        hooks = snapshot.get("webhooks", {})
        if isinstance(hooks, dict) and hooks.get("state") == "ready":
            self._klippy_recovered()
        elif isinstance(hooks, dict) and hooks.get("state") in {"disconnected", "startup"}:
            self._klippy_disconnected()
        elif isinstance(hooks, dict) and hooks.get("state") in {"shutdown", "error"}:
            self.klippy_down_since = None
            self.shutdown_query_since = None

    def _klippy_disconnected(self, source_method=None):
        if self.klippy_down_since is None:
            self.klippy_down_since = time.monotonic()
            self.disconnect_method = source_method

    def _klippy_recovered(self, source_method=None):
        self.klippy_down_since = None
        self.disconnect_method = None
        if self.klippy_down_alerted:
            self._system_alert("klippy_recovered", self.klippy_down_id,
                               "Moonraker reports Kalico ready again; check before restarting print", "info",
                               source_method=source_method, trigger="state_recovery")
            self.klippy_down_alerted = False

    def _finish_shutdown(self, processor, hooks=None, cause=None):
        if hooks is None and self.shutdown_query_since is None:
            return
        if hooks is None:
            hooks = {"state": "shutdown", "state_message":
                     "Kalico Shutdown event received; state query did not return details"}
        # Partial queries are not subscription acknowledgements and must not
        # overwrite persisted job identity with an empty print_stats object.
        previous = processor.previous or {}
        if not processor.has_snapshot:
            processor.status = {"print_stats": {"state": previous.get("state", ""),
                                "filename": previous.get("filename", "")}}
            processor.has_snapshot = True
        current, alerts = processor.apply_update(
            {"webhooks": hooks}, time.monotonic(),
            source_method=cause or "notify_klippy_shutdown",
            trigger="state_query" if cause else "shutdown_query_timeout")
        self._record(processor, current, alerts)
        self.shutdown_query_since = None

    def _tick(self, processor: EventProcessor):
        self._heartbeat()
        if (self.shutdown_query_since is not None and
                time.monotonic() - self.shutdown_query_since >= 3):
            self._finish_shutdown(processor)
        if self.subscription_active and processor.has_snapshot:
            connected = (
                self.klippy_down_since is None
                and self.shutdown_query_since is None
                and self.last_status_received_at is not None
                and time.monotonic() - self.last_status_received_at <= 120
            )
            fresh = time.monotonic()
            progress_seen = self.field_seen.get(("virtual_sdcard", "file_position"), float("-inf"))
            stall_fresh = connected and 0 <= fresh - progress_seen <= 120
            if not stall_fresh and processor.previous:
                processor.previous = {**processor.previous, "progress_changed_at": fresh}
            current, alerts = processor.evaluate(fresh, allow_stall=stall_fresh)
            self._record(processor, current, alerts)
            if self.reporter.evaluate(current, processor.status, time.time(), connected=connected):
                LOG.info("periodic progress report queued")
            if self.rule_engine.evaluate(
                processor.status, time.monotonic(), field_seen=self.field_seen,
                connected=connected and current.get("klippy_state") == "ready",
                generation=current.get("generation", 0),
                filename=current.get("filename", ""),
            ):
                LOG.info("custom rule alert queued")
        if (self.klippy_down_since is not None and not self.klippy_down_alerted
                and time.monotonic() - self.klippy_down_since >= self.settings.disconnect_seconds):
            self.klippy_down_id = self._new_incident("klippy_incidents")
            self._system_alert("klippy_disconnected", self.klippy_down_id,
                               "Moonraker has lost its connection to Kalico",
                               source_method=self.disconnect_method, trigger="disconnect_confirmed")
            self.klippy_down_alerted = True

    async def _rule_objects(self, session: aiohttp.ClientSession, headers: dict) -> tuple[str, ...]:
        """Only subscribe to existing optional objects; never block base monitoring."""
        desired = self.rule_engine.objects
        if not desired:
            return ()
        try:
            async with session.get(self.settings.moonraker_url + "/printer/objects/list",
                                   headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as response:
                response.raise_for_status()
                body = await response.json()
            available = set(body.get("result", {}).get("objects", []))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, AttributeError):
            LOG.warning("custom rule object discovery failed; base monitoring continues")
            return ()
        missing = sorted(set(desired) - available)
        for name in missing:
            LOG.warning("custom rule object unavailable: %s", name)
        return tuple(name for name in desired if name in available)

    def _subscriptions(self, extras: tuple[str, ...]) -> list[dict]:
        force = self.settings.watch_nozzle_force
        candidates = [
            subscribe_request(force, extra_objects=extras),
            subscribe_request(force),
            subscribe_request(False, extra_objects=extras),
            subscribe_request(False),
        ]
        result = []
        for item in candidates:
            if item not in result:
                result.append(item)
        return result

    async def _listen(self, session: aiohttp.ClientSession):
        headers = {"X-Api-Key": self.settings.moonraker_api_key} if self.settings.moonraker_api_key else {}
        processor = EventProcessor(previous=self.store.get_state(),
                                   stall_minutes=self.settings.stall_minutes)
        self._invalidate_subscription()
        rule_objects = await self._rule_objects(session, headers)
        subscriptions = self._subscriptions(rule_objects)
        async with session.ws_connect(self.settings.ws_url, headers=headers,
                                      heartbeat=20) as ws:
            LOG.info("Moonraker connected; subscribing to status")
            subscription_index = 0
            request_id = 1
            pending = True
            await ws.send_json({**subscriptions[subscription_index], "id": request_id})
            requested_at = time.monotonic()
            last_refresh = requested_at
            while True:
                if self.subscription_active and not pending and time.monotonic() - last_refresh >= 30:
                    request_id = max(10, request_id + 1)
                    pending = True
                    requested_at = last_refresh = time.monotonic()
                    await ws.send_json({**subscriptions[subscription_index], "id": request_id})
                if pending and time.monotonic() - requested_at > 20:
                    raise TimeoutError("Moonraker subscription reply timed out")
                try:
                    msg = await ws.receive(timeout=5)
                except asyncio.TimeoutError:
                    self._tick(processor)
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
                                aiohttp.WSMsgType.CLOSE):
                    self._finish_shutdown(processor)
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
                if pending and reply_id == request_id:
                    # A subscription reply is the complete initial snapshot.
                    if "error" in packet:
                        self._invalidate_subscription()
                        if subscription_index + 1 < len(subscriptions):
                            subscription_index += 1
                            requested_at = time.monotonic()
                            LOG.warning("Optional subscription rejected; trying variant %s",
                                        subscription_index)
                            request_id = max(10, request_id + 1)
                            await ws.send_json({**subscriptions[subscription_index], "id": request_id})
                            continue
                        raise RuntimeError("Moonraker subscription rejected")
                    result = packet.get("result", {})
                    snapshot = result.get("status") if isinstance(result, dict) else None
                    if not isinstance(snapshot, dict):
                        raise RuntimeError("Moonraker subscription returned no status")
                    if (not all(isinstance(snapshot.get(obj), dict) for obj in
                                ("print_stats", "webhooks", "virtual_sdcard"))
                            or not isinstance(snapshot["print_stats"].get("state"), str)
                            or not isinstance(snapshot["webhooks"].get("state"), str)):
                        raise RuntimeError("Moonraker subscription missing core objects")
                    pending = False
                    last_refresh = time.monotonic()
                    self._process_snapshot(processor, snapshot)
                    continue
                if reply_id == 2:
                    if self.subscription_active or (self.shutdown_query_since is None and self.klippy_down_since is None):
                        continue
                    # A Klippy failure may reject object queries while Moonraker
                    # itself remains reachable. Keep the WebSocket connected.
                    if "error" in packet:
                        LOG.warning("Moonraker webhooks query rejected; keeping connection")
                        continue
                    result = packet.get("result", {})
                    snapshot = result.get("status") if isinstance(result, dict) else None
                    if isinstance(snapshot, dict) and isinstance(snapshot.get("webhooks"), dict):
                        update = {"webhooks": snapshot["webhooks"]}
                        cause = ("notify_klippy_shutdown" if self.shutdown_query_since is not None
                                 else self.disconnect_method)
                        hooks_state = snapshot["webhooks"].get("state")
                        if hooks_state in {"shutdown", "error"}:
                            self._finish_shutdown(processor, update["webhooks"], cause)
                        if hooks_state in {"shutdown", "error"}:
                            self.shutdown_query_since = None
                            self.klippy_down_since = None
                        elif hooks_state == "ready":
                            # Recovery requires a fresh full subscription, not a partial query.
                            self._finish_shutdown(processor)
                        elif hooks_state in {"disconnected", "startup"}:
                            self._klippy_disconnected()
                    continue

                self.native_events.observe(packet)
                method = packet.get("method")
                if method == "notify_klippy_shutdown":
                    self._invalidate_subscription()
                    pending = False
                    if self.shutdown_query_since is None:
                        self.shutdown_query_since = time.monotonic()
                    await ws.send_json(QUERY)
                elif method == "notify_klippy_disconnected":
                    self._invalidate_subscription()
                    pending = False
                    self._klippy_disconnected(method)
                    await ws.send_json(QUERY)
                elif method == "notify_klippy_ready":
                    self._finish_shutdown(processor)
                    self._invalidate_subscription()
                    rule_objects = await self._rule_objects(session, headers)
                    subscriptions = self._subscriptions(rule_objects)
                    subscription_index = 0
                    requested_at = time.monotonic()
                    request_id = max(10, request_id + 1)
                    pending = True
                    await ws.send_json({**subscriptions[subscription_index], "id": request_id})
                else:
                    if method == "notify_status_update" and not self.subscription_active:
                        self._tick(processor)
                        continue
                    params = packet.get("params")
                    if method == "notify_status_update" and isinstance(params, list) and params and isinstance(params[0], dict):
                        self._remember_fields(params[0])
                    current, alerts = processor.handle_packet(packet, time.monotonic())
                    if current is not None:
                        if method == "notify_status_update":
                            self.last_status_received_at = time.monotonic()
                        self._record(processor, current, alerts)
                        hooks = processor.status.get("webhooks", {})
                        if hooks.get("state") == "ready":
                            self._klippy_recovered(method)
                        elif hooks.get("state") in {"disconnected", "startup"}:
                            self._klippy_disconnected(method)
                self._tick(processor)

    async def _listen_forever(self, session):
        while True:
            try:
                await self._listen(session)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, OSError, TimeoutError,
                    ConnectionError, RuntimeError) as exc:
                self._invalidate_subscription("disconnected")
                LOG.warning("Moonraker WebSocket problem: %s", type(exc).__name__)
                if self.ws_offline_since is None:
                    self.ws_offline_since = time.monotonic()
                if (not self.ws_offline_notified and
                        time.monotonic() - self.ws_offline_since >= self.settings.disconnect_seconds):
                    self.ws_offline_id = self._new_incident("moonraker_incidents")
                    self._system_alert("moonraker_unreachable", self.ws_offline_id,
                                       "Moonraker WebSocket is unavailable")
                    self.ws_offline_notified = True
                self._heartbeat()
            await asyncio.sleep(3)

    async def run(self):
        headers = {"User-Agent": "hermes-plugin-kalico-monitor/0.3"}
        async with aiohttp.ClientSession(headers=headers, trust_env=False) as session:
            await self._supervise(self._dispatch(session), self._listen_forever(session))



def main():
    # Local configuration is explicit and never starts the monitor daemon.
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        from .setup import main as config_main
        config_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] in {"events", "discover", "test", "handlers"}:
        from .event_cli import main as event_main
        event_main(sys.argv[1:])
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
