"""Deterministic simulated periodic printing reports (no printer actuation)."""
import pytest

from kalico_monitor.config import Settings
from kalico_monitor.events import Alert
from kalico_monitor.monitor import Monitor
from kalico_monitor.reports import PeriodicReporter, report_detail
from kalico_monitor.storage import Outbox


def sample_state(generation=1, state="printing", klippy_state="ready"):
    return {"generation": generation, "state": state,
            "klippy_state": klippy_state, "filename": "peek.gcode"}


def sample_status():
    return {
        "print_stats": {"state": "printing", "filename": "peek.gcode",
                        "print_duration": 4500, "info": {"current_layer": 18, "total_layer": 120}},
        "virtual_sdcard": {"progress": 0.125},
        "extruder": {"temperature": 399.2, "target": 400},
        "heater_bed": {"temperature": 119.8, "target": 120},
        "webhooks": {"state": "ready"},
    }


def test_report_contains_observed_printing_fields():
    detail = report_detail(sample_status())
    assert "peek.gcode" in detail
    assert "12.5%" in detail
    assert "1 小时 15 分钟" in detail
    assert "399.2 / 400.0" in detail
    assert "119.8 / 120.0" in detail
    assert "18 / 120" in detail
    assert "成型质量正常" not in detail


def test_missing_and_invalid_fields_do_not_fabricate_values():
    detail = report_detail({
        "print_stats": {"state": "printing", "filename": ""},
        "virtual_sdcard": {"progress": 8.3},
        "extruder": {"temperature": True},
    })
    assert "进度：未知" in detail
    assert "已打印：未知" in detail
    assert "喷嘴：— / —" in detail
    assert "层数" not in detail


def test_periodic_disabled_even_when_printing(tmp_path):
    db = Outbox(tmp_path / "off.sqlite3", "PEEK-01")
    try:
        scheduler = PeriodicReporter(db, 0)
        for now in (10, 2000, 10000):
            assert not scheduler.evaluate(sample_state(), sample_status(), now)
        assert db.summary()["pending"] == 0
    finally:
        db.close()


def test_periodic_queue_and_restart_deduplicate(tmp_path):
    path = tmp_path / "reports.sqlite3"
    db = Outbox(path, "PEEK-01")
    try:
        scheduler = PeriodicReporter(db, 30)
        status = sample_status()
        state = sample_state()
        assert not scheduler.evaluate(state, status, 100)
        assert not scheduler.evaluate(state, status, 1899)
        assert scheduler.evaluate(state, status, 1900)
        first = db.pending()[0]
        assert first["kind"] == "print_progress"
        assert first["severity"] == "info"
        assert "打印过程定时报告" in first["detail"]
        assert not scheduler.evaluate(state, status, 1900)
        # A process restart must not enqueue the same report a second time.
        restarted = PeriodicReporter(db, 30)
        assert not restarted.evaluate(state, status, 1901)
        assert restarted.evaluate(state, status, 3700)
        ids = [item["id"] for item in db.pending()]
        assert len(ids) == 2 and len(set(ids)) == 2
        assert restarted.tracker["sequence"] == 2
    finally:
        db.close()


def test_pause_and_disconnect_reset_countdown(tmp_path):
    db = Outbox(tmp_path / "pause.sqlite3", "PEEK-01")
    try:
        scheduler = PeriodicReporter(db, 30)
        status = sample_status()
        state = sample_state()
        assert not scheduler.evaluate(state, status, 100)
        assert not scheduler.evaluate(sample_state(state="paused"), status, 1500)
        assert not scheduler.evaluate(state, status, 1900)
        assert not scheduler.evaluate(state, status, 3699)
        assert scheduler.evaluate(state, status, 3700)
        assert not scheduler.evaluate(state, status, 3701, connected=False)
        assert not scheduler.evaluate(state, status, 5500)
        assert not scheduler.evaluate(state, status, 7299)
        assert scheduler.evaluate(state, status, 7300)
        assert len(db.pending()) == 2
    finally:
        db.close()


def test_no_reports_on_shutdown_or_klippy_disconnect(tmp_path):
    db = Outbox(tmp_path / "stopped.sqlite3", "PEEK-01")
    try:
        scheduler = PeriodicReporter(db, 5)
        status = sample_status()
        assert not scheduler.evaluate(sample_state(klippy_state="shutdown"), status, 100)
        assert not scheduler.evaluate(sample_state(klippy_state="ready"), status, 200)
        assert not scheduler.evaluate(sample_state(klippy_state="disconnected"), status, 500)
        assert not scheduler.evaluate(sample_state(), status, 501)
        assert not scheduler.evaluate(sample_state(), status, 800)
        assert scheduler.evaluate(sample_state(), status, 801)
    finally:
        db.close()


def test_new_generation_same_filename_does_not_reuse_old_schedule(tmp_path):
    db = Outbox(tmp_path / "new.sqlite3", "PEEK-01")
    try:
        scheduler = PeriodicReporter(db, 5)
        status = sample_status()
        assert not scheduler.evaluate(sample_state(1), status, 100)
        assert scheduler.evaluate(sample_state(1), status, 400)
        assert not scheduler.evaluate(sample_state(2), status, 401)
        assert not scheduler.evaluate(sample_state(2), status, 700)
        assert scheduler.evaluate(sample_state(2), status, 701)
        pending = db.pending()
        assert len(pending) == 2
        assert pending[0]["id"] != pending[1]["id"]
    finally:
        db.close()



def test_websocket_loss_suspends_pending_report_without_losing_sequence(tmp_path):
    path = tmp_path / "offline.sqlite3"
    db = Outbox(path, "PEEK-01")
    try:
        scheduler = PeriodicReporter(db, 5)
        state, status = sample_state(), sample_status()
        assert not scheduler.evaluate(state, status, 100)
        assert scheduler.evaluate(state, status, 400)
        scheduler.suspend()
        assert scheduler.tracker["next_due_at"] is None
        after_restart = PeriodicReporter(db, 5)
        assert not after_restart.evaluate(state, status, 1200)
        assert not after_restart.evaluate(state, status, 1499)
        assert after_restart.evaluate(state, status, 1500)
        assert after_restart.tracker["sequence"] == 2
        assert len(db.pending()) == 2
    finally:
        db.close()


def test_progress_alert_does_not_duplicate_critical_alert(tmp_path):
    db = Outbox(tmp_path / "alerts.sqlite3", "PEEK-01")
    try:
        scheduler = PeriodicReporter(db, 5)
        scheduler.evaluate(sample_state(), sample_status(), 100)
        scheduler.evaluate(sample_state(), sample_status(), 400)
        db.enqueue(Alert(kind="klippy_shutdown", generation=1,
                         filename="peek.gcode", detail="SHT36 fault", severity="critical"))
        kinds = [row["kind"] for row in db.pending()]
        assert kinds == ["print_progress", "klippy_shutdown"]
        assert db.summary()["pending"] == 2
    finally:
        db.close()


def test_settings_interval_validation(monkeypatch):
    monkeypatch.setenv("HERMES_WEBHOOK_SECRET", "test-secret")
    for raw, expected in (("0", 0), ("5", 5), ("30", 30), ("1440", 1440)):
        monkeypatch.setenv("KALICO_PROGRESS_REPORT_MINUTES", raw)
        assert Settings.load().progress_report_minutes == expected
    for raw in ("-1", "1", "4", "1441"):
        monkeypatch.setenv("KALICO_PROGRESS_REPORT_MINUTES", raw)
        with pytest.raises(ValueError):
            Settings.load()


def test_monitor_includes_progress_subscription_and_heartbeat(tmp_path):
    cfg = Settings(
        printer="PEEK-01", moonraker_url="http://127.0.0.1:7125",
        moonraker_api_key="", hermes_url="http://127.0.0.1:8644/webhooks/test",
        hermes_secret="test-secret", sqlite_path=tmp_path / "test.sqlite3",
        disconnect_seconds=25, stall_minutes=0, progress_report_minutes=30,
    )
    db = Outbox(cfg.sqlite_path, cfg.printer)
    try:
        monitor = Monitor(cfg, db)
        assert monitor.reporter.interval_seconds == 1800
        from kalico_monitor.monitor import SUBSCRIBE

        for key in ("print_stats", "virtual_sdcard", "webhooks", "extruder", "heater_bed"):
            assert key in SUBSCRIBE["params"]["objects"]
    finally:
        db.close()
