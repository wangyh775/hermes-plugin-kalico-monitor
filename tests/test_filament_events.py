import json

from kalico_monitor.filament_events import FilamentEventTracker
from kalico_monitor.monitor import subscribe_request
from kalico_monitor.notifier import make_request
from kalico_monitor.storage import Outbox


def event(seq=1, boot="a" * 32):
    return {
        "schema": 1, "boot_id": boot, "seq": seq,
        "event_id": f"{boot}:{seq}", "kind": "jam_detected",
        "source": "hard", "force_g": 4200.0, "layer": 1,
        "window": {"samples": [[0, 10], [0.1, 4200]],
                   "duration_s": 0.1, "sample_count": 2},
    }


def test_journal_replay_is_deduplicated_across_restart(tmp_path):
    db = tmp_path / "alerts.db"
    state = {"generation": 2, "filename": "test.gcode"}
    e = event()
    status = {"filament_force": {"events": [e]}}
    store = Outbox(db, "printer")
    assert FilamentEventTracker(store).observe(status, state) == 1
    store.close()
    store = Outbox(db, "printer")
    try:
        tracker = FilamentEventTracker(store)
        assert tracker.observe(status, {"generation": 3}) == 0
        assert tracker.observe({"filament_force": {"events": [e, event(2)]}}, state) == 1
        assert tracker.observe({"filament_force": {"events": [event(1, "b" * 32)]}}, state) == 1
        rows = store.pending()
        assert len(rows) == 3
        alert = next(row for row in rows if json.loads(row["payload"]) == e)
        body, headers = make_request(alert, "secret", "printer", timestamp=1234)
        assert json.loads(body)["event"]["window"]["samples"] == e["window"]["samples"]
        assert "X-Webhook-Signature-V2" in headers
    finally:
        store.close()


def test_malformed_events_and_subscription(tmp_path):
    store = Outbox(tmp_path / "alerts.db", "printer")
    try:
        bad = event()
        bad["window"]["samples"] = [[0, float("nan")]]
        assert FilamentEventTracker(store).observe({"filament_force": {"events": [bad]}}, {}) == 0
        assert store.pending() == []
    finally:
        store.close()
    assert subscribe_request(True)["params"]["objects"]["filament_force"] == ["events"]
    assert "filament_force" not in subscribe_request(False)["params"]["objects"]
    assert "nozzle_force_monitor" not in subscribe_request(True, filament_only=True)["params"]["objects"]


def test_cursor_detects_gap_after_restart_and_replay_is_idempotent(tmp_path):
    path = tmp_path / "alerts.db"
    state = {"generation": 2, "filename": "test.gcode"}
    def snapshot(*events):
        return {"filament_force": {"events": list(events)}}
    store = Outbox(path, "printer")
    assert FilamentEventTracker(store).observe(snapshot(event(20)), state) == 1
    assert not [r for r in store.pending() if r["kind"] == "filament_event_gap"]
    store.close()
    store = Outbox(path, "printer")
    try:
        tracker = FilamentEventTracker(store)
        assert tracker.observe(snapshot(event(23), event(24)), state) == 2
        assert tracker.observe(snapshot(event(23), event(24)), state) == 0
        gaps = [r for r in store.pending() if r["kind"] == "filament_event_gap"]
        assert len(gaps) == 1
        assert json.loads(gaps[0]["payload"])["missing_count"] == 2
        assert tracker.observe(snapshot(event(21), event(22)), state) == 2
        assert store.get_meta("filament_cursor:printer:" + "a" * 32) == 24
        assert tracker.observe(snapshot(event(1, "b" * 32)), state) == 1
        assert len([r for r in store.pending() if r["kind"] == "filament_event_gap"]) == 1
    finally:
        store.close()


def test_gap_within_snapshot_and_sensor_fault(tmp_path):
    store = Outbox(tmp_path / "db", "printer")
    try:
        sensor = event(3)
        sensor["kind"] = "sensor_fault"
        sensor["source"] = "watchdog"
        sensor["reason"] = "load cell stream stale"
        tracker = FilamentEventTracker(store)
        assert tracker.observe({"filament_force": {"events": [sensor, event(1)]}}, {}) == 2
        assert {r["kind"] for r in store.pending()} == {
            "filament_jam_detected", "filament_sensor_fault", "filament_event_gap"}
        assert next(r for r in store.pending() if r["kind"] == "filament_sensor_fault")["severity"] == "critical"
    finally:
        store.close()
