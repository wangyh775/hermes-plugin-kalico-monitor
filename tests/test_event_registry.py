import json

from kalico_monitor.event_registry import BUILTINS, NATIVE, SOURCE_TYPES
from kalico_monitor.events import Alert, EventProcessor
from kalico_monitor.native_events import NativeEventAdapter
from kalico_monitor.notifier import make_request
from kalico_monitor.policy import parse_modes, routes_for
from kalico_monitor.storage import Outbox


def test_registry_policy():
    assert len(BUILTINS) == 25
    assert len(SOURCE_TYPES) == 6
    assert routes_for("notify_history_changed", parse_modes("default=both")) == ()
    assert routes_for("notify_history_changed", parse_modes("notify_history_changed=agent")) == (
        "agent",
    )
    assert routes_for("notify_status_update", parse_modes("notify_status_update=both")) == ()
    assert not NATIVE["notify_user_created"].forwardable


def test_native_adapter_and_persisted_origin(tmp_path):
    db = Outbox(tmp_path / "events.db", "PEEK")
    try:
        adapter = NativeEventAdapter(db, parse_modes("notify_history_changed=both"))
        packet = {"method": "notify_history_changed", "params": [{"action": "added"}]}
        assert adapter.observe(packet, 100)
        assert not adapter.observe(packet, 101)
        assert not adapter.observe({**packet, "id": 1}, 140)
        assert not adapter.observe({**packet, "params": ["x" * 20000]}, 140)
        assert not adapter.observe({"method": "notify_status_update"}, 140)
        assert adapter.observe(packet, 140)
        row = db.unrouted()[0]
        body, _ = make_request(row, "secret", "PEEK", timestamp=100, route="agent")
        event = json.loads(body)
        assert event["source_type"] == "moonraker_native"
        assert event["source_method"] == "notify_history_changed"
        assert event["event"]["params"] == packet["params"]
    finally:
        db.close()


def test_lifecycle_origin_and_legacy_payload(tmp_path):
    p = EventProcessor()
    p.set_snapshot({"print_stats": {"state": "printing", "filename": "a.gcode"},
                    "webhooks": {"state": "ready"}}, 100)
    _, alerts = p.handle_packet({"method": "notify_status_update",
                                 "params": [{"print_stats": {"state": "paused"}}]}, 101)
    assert alerts[0].source_method == "notify_status_update"
    db = Outbox(tmp_path / "events.db", "PEEK")
    try:
        db.enqueue(alerts[0])
        db.enqueue(Alert("filament_jam_confirmed", 1, "a", "jam", "critical",
                         payload={"window": {"samples": [[0, 10]]}}))
        rows = {row["kind"]: row for row in db.unrouted()}
        body, _ = make_request(rows["paused"], "secret", "PEEK", route="agent")
        assert json.loads(body)["source_type"] == "moonraker_lifecycle"
        body, _ = make_request(rows["filament_jam_confirmed"], "secret", "PEEK", route="agent")
        data = json.loads(body)
        assert data["source_type"] == "klipper_extension"
        assert data["event"]["window"]["samples"] == [[0, 10]]
    finally:
        db.close()
