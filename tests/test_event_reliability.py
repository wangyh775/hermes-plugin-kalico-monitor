"""Fault-injection regression coverage for the event pipeline (no real printer)."""
import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest

from kalico_monitor import monitor as monitor_module
from kalico_monitor.config import Settings
from kalico_monitor.custom_config import EventRule, load_extensions
from kalico_monitor.events import Alert, EventProcessor
from kalico_monitor.monitor import Monitor
from kalico_monitor.native_events import NativeEventAdapter
from kalico_monitor.notifier import deliver, make_request
from kalico_monitor.policy import parse_modes
from kalico_monitor.reports import PeriodicReporter
from kalico_monitor.rule_engine import RuleEngine
from kalico_monitor.storage import Outbox

SNAPSHOT = {
    "print_stats": {"state": "printing", "filename": "a.gcode"},
    "webhooks": {"state": "ready"},
    "virtual_sdcard": {"file_position": 10, "progress": 0.1},
    "extruder": {"temperature": 400, "target": 400},
    "heater_bed": {"temperature": 120, "target": 120},
}


def settings(tmp_path, **kwargs):
    return Settings(printer="PEEK", moonraker_url="http://localhost:7125", moonraker_api_key="",
                    hermes_url="http://localhost:8644/webhooks/kalico-alert",
                    hermes_agent_url="http://localhost:8644/webhooks/kalico-analysis",
                    hermes_secret="secret", sqlite_path=tmp_path / "events.db",
                    disconnect_seconds=5, stall_minutes=1, **kwargs)


@pytest.mark.asyncio
async def test_supervisor_propagates_failure_and_cancels_peer():
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def peer():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def fail():
        await started.wait()
        raise TypeError("broken sender")

    with pytest.raises(TypeError, match="broken sender"):
        await Monitor._supervise(peer(), fail())
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_invalid_webhook_response_is_retryable():
    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        async def json(self):
            return {"status": []}

    session = SimpleNamespace(post=lambda *a, **kw: Response())
    alert = {"id": "x", "kind": "paused", "severity": "warning", "filename": "a",
             "detail": "pause", "created": 1}
    with pytest.raises(ValueError, match="did not confirm"):
        await deliver(session, "http://localhost", "secret", "PEEK", alert)


@pytest.mark.asyncio
async def test_slow_agent_does_not_block_direct_and_critical_priority(tmp_path, monkeypatch):
    cfg = settings(tmp_path, event_modes="default=both")
    db = Outbox(cfg.sqlite_path, cfg.printer)
    blocked = asyncio.Event()
    sent = asyncio.Event()
    direct = []

    async def fake(_session, _url, _secret, _printer, item, route, **kwargs):
        if route == "agent":
            blocked.set()
            await asyncio.Future()
        direct.append(item["kind"])
        sent.set()
        return {"status": "delivered"}

    monkeypatch.setattr(monitor_module, "deliver", fake)
    db.enqueue(Alert("print_progress", 1, "a", "report", "info"))
    db.enqueue(Alert("filament_jam_confirmed", 1, "a", "jam", "critical"))
    task = asyncio.create_task(Monitor(cfg, db)._dispatch(None))
    try:
        await asyncio.wait_for(blocked.wait(), 1)
        await asyncio.wait_for(sent.wait(), 1)
        assert direct[0] == "filament_jam_confirmed"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        db.close()


@pytest.mark.asyncio
async def test_ready_resubscribe_timeout_even_after_valid_snapshot(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    clock = [0.0]
    monkeypatch.setattr(monitor_module, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=lambda: 1700000000 + clock[0]))

    class Socket:
        def __init__(self):
            self.sent = []
            self.count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send_json(self, request):
            self.sent.append(request)

        async def receive(self, **kwargs):
            self.count += 1
            if self.count == 1:
                packet = {"id": self.sent[-1]["id"], "result": {"status": SNAPSHOT}}
            elif self.count == 2:
                packet = {"method": "notify_klippy_ready"}
            else:
                clock[0] += 6
                packet = {"id": self.sent[0]["id"], "result": {"status": SNAPSHOT}}
            return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(packet))

    socket = Socket()
    session = SimpleNamespace(ws_connect=lambda *a, **kw: socket)
    try:
        monitor = Monitor(cfg, db)
        with pytest.raises(TimeoutError, match="subscription reply"):
            await monitor._listen(session)
        assert len(socket.sent) == 2
        assert socket.sent[0]["id"] != socket.sent[1]["id"]
        assert not monitor.subscription_active
    finally:
        db.close()


def test_threshold_restart_and_stale_observation(tmp_path):
    rule = EventRule("hot", "extruder", "temperature", "gt", 390, 10, 0,
                     "warning", "direct")
    db = Outbox(tmp_path / "events.db", "PEEK")
    try:
        engine = RuleEngine(db, (rule,))
        assert engine.evaluate(SNAPSHOT, 100) == 0
        engine.suspend()
        assert engine.evaluate(SNAPSHOT, 200) == 0
        assert engine.evaluate(SNAPSHOT, 209) == 0
        engine = RuleEngine(db, (rule,))
        assert engine.evaluate(SNAPSHOT, 300) == 0
        assert engine.evaluate(SNAPSHOT, 310, field_seen={("extruder", "temperature"): 100}) == 0
        assert engine.evaluate(SNAPSHOT, 311, field_seen={("extruder", "temperature"): 311}) == 0
        assert engine.evaluate(SNAPSHOT, 321, field_seen={("extruder", "temperature"): 320}) == 1
    finally:
        db.close()


def test_progress_interval_does_not_reuse_ids(tmp_path):
    db = Outbox(tmp_path / "events.db", "PEEK")
    state = {"generation": 1, "filename": "a.gcode", "state": "printing", "klippy_state": "ready"}
    try:
        reporter = PeriodicReporter(db, 5)
        assert not reporter.evaluate(state, SNAPSHOT, 10)
        assert reporter.evaluate(state, SNAPSHOT, 310)
        reporter = PeriodicReporter(db, 10)
        assert not reporter.evaluate(state, SNAPSHOT, 320)
        assert reporter.evaluate(state, SNAPSHOT, 920)
        assert len(db.unrouted()) == 2
        assert reporter.tracker["sequence"] == 2
    finally:
        db.close()


def test_native_transitions_not_suppressed_by_method(tmp_path):
    db = Outbox(tmp_path / "events.db", "PEEK")
    adapter = NativeEventAdapter(db, parse_modes("notify_service_state_changed=direct"))

    def packet(name, state):
        return {"method": "notify_service_state_changed",
                "params": [{name: {"active_state": state, "sub_state": "dead"}}]}

    try:
        assert adapter.observe(packet("a", "active"), 100)
        assert adapter.observe(packet("b", "failed"), 100.1)
        assert adapter.observe(packet("a", "active"), 100.2)
        assert not adapter.observe(packet("a", "active"), 100.3)
        rows = db.unrouted()
        assert len(rows) == 3
        assert rows[0]["severity"] == "critical"
        assert json.loads(rows[0]["payload"])["service"] == "b"
    finally:
        db.close()


@pytest.mark.parametrize("config", [
    "[events]\nfilament_jam_confimed: both\n",
    "[events]\nnotify_status_update: both\n",
    "[message nonexistent]\nfields: printer\n",
    "[events]\nnozzle_force_anomaly: direct\n",
])
def test_registry_config_rejects_invalid_references(tmp_path, config):
    path = tmp_path / "monitor.cfg"
    path.write_text(config)
    with pytest.raises(ValueError):
        load_extensions(path)


def test_capability_and_incident_survive_restart(tmp_path):
    cfg = settings(tmp_path, watch_nozzle_force=True)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    try:
        first = Monitor(cfg, db)
        first._process_snapshot(EventProcessor(), SNAPSHOT)
        assert db.get_meta("subscription_health")["state"] == "degraded"
        first._system_alert("moonraker_unreachable", 7, "offline")
        second = Monitor(cfg, db)
        assert second.ws_offline_notified and second.ws_offline_id == 7
        second._process_snapshot(EventProcessor(), {**SNAPSHOT, "filament_force": {"events": []}})
        assert db.get_meta("subscription_health")["state"] == "active"
        assert db.get_meta("moonraker_open_incident") == {}
        kinds = {row["kind"] for row in db.unrouted()}
        assert {"monitor_degraded", "monitor_recovered", "moonraker_recovered"} <= kinds
        recovered = next(row for row in db.unrouted() if row["kind"] == "moonraker_recovered")
        assert json.loads(recovered["payload"])["incident_id"] == 7
    finally:
        db.close()


def test_lifecycle_payload_and_timestamps(tmp_path):
    db = Outbox(tmp_path / "events.db", "PEEK")
    try:
        processor = EventProcessor()
        processor.set_snapshot(SNAPSHOT, 10)
        _, alerts = processor.apply_update({"print_stats": {"state": "paused"}}, 11)
        db.enqueue(alerts[0])
        row = db.unrouted()[0]
        body, _ = make_request(row, "secret", "PEEK", message_fields={
            "paused": ("previous_state", "current_state", "nozzle", "queued_at")})
        event = json.loads(body)
        assert event["event"]["previous_state"] == "printing"
        assert event["event"]["current_state"] == "paused"
        assert "400.0" in event["message"]
        assert event["queued_at"].endswith("+00:00")
        assert event["received_at"].endswith("+00:00")
        assert event["source_occurred_at"] is None
    finally:
        db.close()
