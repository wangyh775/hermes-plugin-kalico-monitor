"""Fault injection and deterministic SQLite stress cases."""
import json
import time
from dataclasses import replace

from kalico_monitor.config import Settings
from kalico_monitor.events import Alert, EventProcessor
from kalico_monitor.filament_events import FilamentEventTracker
from kalico_monitor.monitor import Monitor
from kalico_monitor.storage import Outbox


def config(tmp_path):
    return Settings("PEEK", "http://localhost:7125", "", "http://localhost:8644",
                    "secret", tmp_path / "alerts.db", 5, 0)


def snapshot(duration=100, position=100, identity="one"):
    return {"print_stats": {"state": "printing", "filename": "a.gcode", "total_duration": duration},
            "webhooks": {"state": "ready"},
            "virtual_sdcard": {"file_position": position},
            "_monitor_job": {"identity": identity}}


def test_shutdown_immediately_durable_and_recovery_ordered(tmp_path):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    processor = EventProcessor()
    monitor = Monitor(cfg, db)
    monitor._process_snapshot(processor, snapshot())
    monitor.shutdown_query_since = time.monotonic()
    monitor._finish_shutdown(processor, {"state": "shutdown", "state_message": "fault"},
                             "notify_klippy_shutdown")
    incident = db.get_meta("shutdown_open_incident")
    assert incident["id"] == 1
    db.close()
    db = Outbox(cfg.sqlite_path, cfg.printer)
    try:
        monitor = Monitor(cfg, db)
        processor = EventProcessor(previous=db.get_state())
        monitor._process_snapshot(processor, snapshot())
        rows = {row["kind"]: row for row in db.unrouted()}
        first, second = rows["klippy_shutdown"], rows["klippy_shutdown_recovered"]
        assert first["incident_key"] == second["incident_key"]
        for item in (first, second):
            db.ensure_routes(item["id"], ("direct", "agent"))
        db.route_failed(first["id"], "direct", "timeout")
        assert not db.pending_routes(route="direct")
        assert db.pending_routes(route="agent")[0]["id"] == first["id"]
        db.route_delivered(first["id"], "direct")
        assert db.pending_routes(route="direct")[0]["id"] == second["id"]
    finally:
        db.close()


def test_poison_event_isolation(tmp_path):
    db = Outbox(tmp_path / "db", "PEEK")
    try:
        bad = Alert("paused", 1, "a", "bad" + chr(0xD800), "warning")
        db.enqueue(bad)
        good = replace(bad, generation=2, detail="good")
        db.enqueue(good)
        assert len(db.unrouted()) == 1
        assert db.conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0] == 1
    finally:
        db.close()


def test_journal_gap_and_more_than_sixteen(tmp_path):
    db = Outbox(tmp_path / "db", "PEEK")
    boot = "a" * 32

    def event(seq):
        return {"schema": 1, "kind": "jam_confirmed", "seq": seq, "boot_id": boot,
                "event_id": f"{boot}:{seq}"}

    try:
        tracker = FilamentEventTracker(db)
        assert tracker.observe({"filament_force": {"events": [event(1)]}}, {}) == 1
        records = [event(i) for i in range(3, 25)]
        assert tracker.observe({"filament_force": {"events": records}}, {}) == 22
        assert tracker.observe({"filament_force": {"events": records}}, {}) == 0
        gap = next(row for row in db.unrouted(limit=100) if row["kind"] == "event_gap_detected")
        assert json.loads(gap["payload"])["missing_from"] == 2
        assert db.get_meta("filament_cursors_v1")[boot] == 24
    finally:
        db.close()


def test_same_filename_restart_uses_history_identity():
    p = EventProcessor()
    p.set_snapshot(snapshot(), 100)
    state, alerts = p.set_snapshot(snapshot(200, 200, "two"), 200)
    assert state["generation"] == 2
    assert [alert.kind for alert in alerts] == ["started"]


def test_database_pressure_and_compaction(tmp_path):
    db = Outbox(tmp_path / "db", "PEEK")
    try:
        for i in range(5000):
            alert = Alert("notify_gcode_response", 0, "", "ok", "info",
                          source_id=str(i), payload={"params": ["ok"]})
            key = db.enqueue(alert)
            db.ensure_routes(key, ("direct",))
            db.route_delivered(key, "direct")
        critical = db.enqueue(Alert("klippy_shutdown", 1, "", "fault", "critical"))
        db.ensure_routes(critical, ("direct",))
        assert db.pending_routes(route="direct")[0]["id"] == critical
        assert db.archive_info(time.time() + 1, limit=1000) == 1000
        assert db.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 5001
        assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.pending_routes(route="direct")[0]["id"] == critical
    finally:
        db.close()
