"""Reliability phase 3: persistence failures, migration and concurrent readers."""
import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import aiohttp
import pytest

from kalico_monitor import monitor as monitor_module
from kalico_monitor.config import Settings
from kalico_monitor.custom_config import load_extensions, load_reliability
from kalico_monitor.events import Alert, EventProcessor
from kalico_monitor.inspect_db import inspect
from kalico_monitor.monitor import Monitor
from kalico_monitor.storage import Outbox


def config(tmp_path):
    return Settings("PEEK", "http://localhost:7125", "", "http://localhost:8644",
                    "secret", tmp_path / "alerts.db", 5, 0)


def snapshot(state="ready", job="a", print_state="printing"):
    return {"print_stats": {"state": print_state, "filename": "a.gcode"},
            "webhooks": {"state": state}, "virtual_sdcard": {"file_position": 10},
            "_monitor_job": {"identity": job}}


def test_critical_degrades_with_original_identity(tmp_path):
    db = Outbox(tmp_path / "db", "PEEK")
    try:
        event = Alert("klippy_shutdown", 1, "a" + chr(0xd800), "fault", "critical",
                      payload={"incident_id": 1, "incident_key": "klippy_shutdown:1",
                               "bad": float("nan")})
        result = db.enqueue_result(event)
        assert result.status == "degraded"
        assert result.id == db.alert_id(event)
        row = db.unrouted()[0]
        assert json.loads(row["payload"])["payload_degraded"]
        assert row["incident_key"] == "klippy_shutdown:1"
        assert db.enqueue_result(event).status == "duplicate"
        assert len(inspect(db.path, "dead-letters", "list")) == 1
    finally:
        db.close()


def test_atomic_recovery_rolls_back_on_checkpoint_failure(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    try:
        monitor = Monitor(cfg, db)
        processor = EventProcessor()
        monitor._process_snapshot(processor, snapshot("shutdown"))
        assert db.get_meta("shutdown_open_incident")["id"] == 1
        original = db._set_meta

        def fail(key, value):
            if key == "shutdown_open_incident" and not value:
                raise sqlite3.OperationalError("injected failure")
            original(key, value)

        with monkeypatch.context() as patch:
            patch.setattr(db, "_set_meta", fail)
            with pytest.raises(sqlite3.OperationalError, match="injected"):
                monitor._process_snapshot(processor, snapshot())
        assert db.get_state()["klippy_state"] == "shutdown"
        assert db.get_meta("shutdown_open_incident")["id"] == 1
        assert [row["kind"] for row in db.unrouted()] == ["klippy_shutdown"]
        monitor = Monitor(cfg, db)
        monitor._process_snapshot(EventProcessor(previous=db.get_state()), snapshot())
        assert db.get_state()["klippy_state"] == "ready"
        assert db.get_meta("shutdown_open_incident") == {}
        assert {row["kind"] for row in db.unrouted()} == {"klippy_shutdown", "klippy_shutdown_recovered"}
    finally:
        db.close()


def test_legacy_incident_backfill_and_route_terminal(tmp_path):
    path = tmp_path / "db"
    db = Outbox(path, "PEEK")
    a = db.enqueue(Alert("moonraker_unreachable", 7, "", "down", "critical",
                         payload={"incident_id": 7}))
    db.ensure_routes(a, ("direct",))
    db.route_failed(a, "direct", "bad" + chr(0xd800))
    with db.conn:
        db.conn.execute("DELETE FROM metadata WHERE key='incident_migration_v1'")
    db.close()
    db = Outbox(path, "PEEK")
    try:
        b = db.enqueue(Alert("moonraker_recovered", 7, "", "ready", "info",
                             payload={"incident_id": 7, "incident_key": "moonraker:7"}))
        db.ensure_routes(b, ("direct",))
        assert not db.pending_routes(route="direct")
        db.route_dead(a, "direct", "HTTP 422")
        assert db.pending_routes(route="direct")[0]["id"] == b
        assert db.conn.execute("SELECT last_error FROM alerts WHERE id=?", (a,)).fetchone()[0] == "permanent failure"
    finally:
        db.close()


def test_new_job_not_resume():
    processor = EventProcessor()
    processor.set_snapshot(snapshot(job="a", print_state="paused"), 10)
    _, alerts = processor.set_snapshot(snapshot(job="b"), 20)
    assert [alert.kind for alert in alerts] == ["started"]


def test_expired_epoch_rejects_late_response(tmp_path):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    try:
        monitor = Monitor(cfg, db)
        socket = object()
        monitor.live_socket = socket
        monitor.connection_epoch = 1
        monitor._check_epoch(socket, 1)
        monitor.connection_epoch += 1
        with pytest.raises(ConnectionError, match="expired"):
            monitor._check_epoch(socket, 1)
    finally:
        db.close()


def test_reliability_cfg_preserved_and_validated(tmp_path):
    path = tmp_path / "monitor.cfg"
    path.write_text("[reliability]\nsubscription_refresh: 10\nobservation_gap: 15\n", encoding="utf-8")
    load_extensions(path)
    assert load_reliability(path).observation_gap == 15
    path.write_text("[reliability]\nsubscription_refresh: 30\nobservation_gap: 20\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_extensions(path)


def test_concurrent_readers_and_bounded_writer_lock(tmp_path):
    db = Outbox(tmp_path / "db", "PEEK")
    try:
        def reader():
            with sqlite3.connect(db.path, timeout=1) as conn:
                for _ in range(50):
                    conn.execute("SELECT count(*) FROM alerts").fetchone()

        with ThreadPoolExecutor(max_workers=3) as pool:
            readers = [pool.submit(reader) for _ in range(3)]
            for i in range(500):
                db.enqueue(Alert("paused", i, "a", "pause", "warning"))
            for future in readers:
                future.result(timeout=5)
        with sqlite3.connect(db.path) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            start = time.monotonic()
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                db.enqueue(Alert("paused", 1000, "a", "pause", "warning"))
            assert time.monotonic() - start < 2
            blocker.rollback()
        assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 500
    finally:
        db.close()


@pytest.mark.asyncio
async def test_watchdog_separate_maintenance_task(tmp_path):
    db = Outbox(config(tmp_path).sqlite_path, "PEEK")
    monitor = Monitor(config(tmp_path), db)
    task = asyncio.create_task(monitor._maintenance())
    try:
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        db.close()


@pytest.mark.asyncio
async def test_late_subscription_ack_cannot_reactivate_invalidated_epoch(tmp_path):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    waiting, release = asyncio.Event(), asyncio.Event()

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send_json(self, packet):
            self.request = packet

        async def receive(self, **kwargs):
            waiting.set()
            await release.wait()
            return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps({
                "id": self.request["id"], "result": {"status": snapshot()}}))

    monitor = Monitor(cfg, db)
    socket = Socket()
    task = asyncio.create_task(monitor._listen(SimpleNamespace(ws_connect=lambda *a, **kw: socket)))
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        monitor.transport_lost = True
        monitor.connection_epoch += 1
        monitor._invalidate_subscription("disconnected")
        release.set()
        with pytest.raises(ConnectionError, match="expired"):
            await task
        assert not monitor.subscription_active
        assert db.get_state() is None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        db.close()


@pytest.mark.asyncio
async def test_terminal_http_rejection_does_not_block_recovery(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    recovered = asyncio.Event()

    async def deliver(_session, _url, _secret, _printer, item, **kwargs):
        if item["kind"] == "moonraker_unreachable":
            raise aiohttp.ClientResponseError(None, (), status=422)
        recovered.set()

    monkeypatch.setattr(monitor_module, "deliver", deliver)
    incident = {"incident_id": 1, "incident_key": "moonraker:1"}
    first = db.enqueue(Alert("moonraker_unreachable", 1, "", "lost", "critical", payload=incident))
    second = db.enqueue(Alert("moonraker_recovered", 1, "", "ready", "info", payload=incident))
    task = asyncio.create_task(Monitor(cfg, db)._dispatch(None))
    try:
        await asyncio.wait_for(recovered.wait(), 2)
        assert db.conn.execute("SELECT suppressed FROM alert_routes WHERE alert_id=?", (first,)).fetchone()[0]
        assert db.conn.execute("SELECT delivered FROM alert_routes WHERE alert_id=?", (second,)).fetchone()[0]
        assert len(inspect(db.path, "dead-letters", "list")) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        db.close()
