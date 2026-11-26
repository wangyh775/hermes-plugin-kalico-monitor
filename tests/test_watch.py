from kalico_monitor.events import EventProcessor
from kalico_monitor.watch import compare, sample_from_status


def status(pstate="standby", kstate="ready", filename="sample.gcode", message=""):
    return {"print_stats": {"state": pstate, "filename": filename, "message": message},
            "webhooks": {"state": kstate, "state_message": message},
            "virtual_sdcard": {"file_position": 100, "progress": 0.2}}


def kinds(events):
    return [x.kind for x in events]


def test_complete_once_and_resume_does_not_increment_generation():
    engine = EventProcessor()
    _, ev = engine.set_snapshot(status(), 100)
    assert ev == []
    p, ev = engine.apply_update({"print_stats": {"state": "printing"}}, 101)
    assert kinds(ev) == ["started"] and p["generation"] == 1
    p, ev = engine.apply_update({"print_stats": {"state": "paused"}}, 102)
    assert kinds(ev) == ["paused"]
    p, ev = engine.apply_update({"print_stats": {"state": "printing"}}, 103)
    assert p["generation"] == 1 and kinds(ev) == ["resumed"]
    p, ev = engine.apply_update({"print_stats": {"state": "complete"}}, 104)
    assert kinds(ev) == ["complete"]
    _, ev = engine.apply_update({"print_stats": {"state": "complete"}}, 105)
    assert ev == []
    assert p["generation"] == 1


def test_new_same_filename_after_complete_is_distinct_job():
    engine = EventProcessor()
    engine.set_snapshot(status(), 0)
    engine.apply_update({"print_stats": {"state": "printing"}}, 1)
    engine.apply_update({"print_stats": {"state": "complete"}}, 2)
    p, _ = engine.apply_update({"print_stats": {"state": "printing"}}, 3)
    assert p["generation"] == 2
    _, events = engine.apply_update({"print_stats": {"state": "complete"}}, 4)
    assert kinds(events) == ["complete"]
    assert events[0].generation == 2


def test_shutdown_has_priority_over_error_and_has_reason():
    engine = EventProcessor()
    engine.set_snapshot(status("printing"), 10)
    engine.ingest_gcode_response("Probing failed after 7 bad probes")
    _, events = engine.apply_update({"webhooks": {"state": "shutdown", "state_message":
                    "MCU 'SHT36' shutdown: Thermocouple reader fault"},
             "print_stats": {"state": "error"}}, 11)
    assert kinds(events) == ["klippy_shutdown"]
    assert "SHT36" in events[0].detail
    assert "Probing failed" in events[0].detail
    _, events = engine.apply_update({"webhooks": {"state": "shutdown"}}, 12)
    assert events == []


def test_startup_shutdown_not_silent():
    engine = EventProcessor()
    _, events = engine.set_snapshot(status(kstate="shutdown", message="heater_fault"), 10)
    assert kinds(events) == ["klippy_shutdown"]


def test_changes_are_incremental_not_full_replacements():
    engine = EventProcessor()
    engine.set_snapshot(status("printing", filename="PEEK.gcode"), 10)
    _, ev = engine.apply_update({"virtual_sdcard": {"progress": 0.3}}, 11)
    assert ev == []
    _, ev = engine.apply_update({"print_stats": {"state": "complete"}}, 12)
    assert kinds(ev) == ["complete"]
    assert ev[0].filename == "PEEK.gcode"


def test_no_alert_on_gcode_keyword_without_stop():
    engine = EventProcessor()
    engine.set_snapshot(status("printing"), 0)
    engine.handle_packet({"method": "notify_gcode_response", "params": ["Bad probe detected. Retrying"]}, 1)
    assert engine.previous["state"] == "printing"


def test_persistent_state_on_reconnection():
    engine = EventProcessor()
    engine.set_snapshot(status("printing"), 100)
    next_engine = EventProcessor(previous=engine.previous)
    _, ev = next_engine.set_snapshot(status("complete"), 102)
    assert kinds(ev) == ["complete"]


def test_stall_is_optional():
    sample = sample_from_status(status("printing"))
    state, _ = compare(None, sample, 100, stall_minutes=0)
    _, ev = compare(state, sample, 500, stall_minutes=0)
    assert ev == []
    state, _ = compare(None, sample, 100, stall_minutes=2)
    _, ev = compare(state, sample, 221, stall_minutes=2)
    assert [x["kind"] for x in ev] == ["stalled"]


def test_start_and_repeated_pause_resume_have_unique_persistent_ids(tmp_path):
    from kalico_monitor.storage import Outbox

    db = Outbox(tmp_path / "alerts.sqlite", "PEEK-01")
    try:
        engine = EventProcessor()
        state, alerts = engine.set_snapshot(status(), 10)
        db.transition(state, alerts)
        state, alerts = engine.apply_update({"print_stats": {"state": "printing"}}, 11)
        assert kinds(alerts) == ["started"]
        db.transition(state, alerts)
        state, alerts = engine.apply_update({"print_stats": {"state": "paused"}}, 12)
        assert kinds(alerts) == ["paused"] and alerts[0].sequence == 1
        db.transition(state, alerts)
        state, alerts = engine.apply_update({"print_stats": {"state": "printing"}}, 13)
        assert kinds(alerts) == ["resumed"] and alerts[0].sequence == 1
        db.transition(state, alerts)

        # Monitor restart preserves counters across the next pause/resume cycle.
        engine = EventProcessor(previous=db.get_state())
        engine.set_snapshot(status("printing"), 14)
        state, alerts = engine.apply_update({"print_stats": {"state": "paused"}}, 15)
        assert kinds(alerts) == ["paused"] and alerts[0].sequence == 2
        db.transition(state, alerts)
        state, alerts = engine.apply_update({"print_stats": {"state": "printing"}}, 16)
        assert kinds(alerts) == ["resumed"] and alerts[0].sequence == 2
        db.transition(state, alerts)
        state, alerts = engine.apply_update({"print_stats": {"state": "complete"}}, 17)
        assert kinds(alerts) == ["complete"]
        db.transition(state, alerts)
        assert [row["kind"] for row in db.pending()] == [
            "started", "paused", "resumed", "paused", "resumed", "complete"
        ]
    finally:
        db.close()


def test_initial_printing_snapshot_does_not_fabricate_start_notification():
    engine = EventProcessor()
    state, alerts = engine.set_snapshot(status("printing"), 10)
    assert alerts == [] and state["generation"] == 1
    _, alerts = engine.apply_update({"print_stats": {"state": "printing"}}, 11)
    assert alerts == []


def test_new_same_file_restarts_emit_new_start_and_complete():
    engine = EventProcessor()
    engine.set_snapshot(status(), 10)
    _, started = engine.apply_update({"print_stats": {"state": "printing"}}, 11)
    _, done = engine.apply_update({"print_stats": {"state": "complete"}}, 12)
    _, again = engine.apply_update({"print_stats": {"state": "printing"}}, 13)
    _, done_again = engine.apply_update({"print_stats": {"state": "complete"}}, 14)
    assert kinds(started) == ["started"] and started[0].generation == 1
    assert kinds(done) == ["complete"] and done[0].generation == 1
    assert kinds(again) == ["started"] and again[0].generation == 2
    assert kinds(done_again) == ["complete"] and done_again[0].generation == 2


def test_force_pause_does_not_filter_paused_notification(tmp_path):
    from kalico_monitor.config import Settings
    from kalico_monitor.monitor import Monitor
    from kalico_monitor.storage import Outbox

    settings = Settings(
        printer="PEEK-01", moonraker_url="http://localhost:7125",
        moonraker_api_key="", hermes_url="http://localhost:8644/webhooks/kalico-alert",
        hermes_secret="secret", sqlite_path=tmp_path / "db.sqlite",
        disconnect_seconds=25, stall_minutes=0, watch_nozzle_force=True,
    )
    db = Outbox(settings.sqlite_path, settings.printer)
    try:
        monitor = Monitor(settings, db)
        engine = EventProcessor()
        state, _ = engine.set_snapshot(status("printing"), 1)
        monitor._record(engine, state, [])
        state, alerts = engine.apply_update({"print_stats": {"state": "paused"}}, 2)
        assert kinds(alerts) == ["paused"]
        # An independent force alert no longer suppresses the state callback.
        monitor._record(engine, state, alerts)
        assert [item["kind"] for item in db.pending()] == ["paused"]
    finally:
        db.close()
