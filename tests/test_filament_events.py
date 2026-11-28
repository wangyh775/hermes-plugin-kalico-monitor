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
