"""Stage D: fault injection against production boundaries, no printer commands."""
import asyncio
import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest

from kalico_monitor.config import Settings
from kalico_monitor.events import Alert
from kalico_monitor.filament_events import FilamentEventTracker
from kalico_monitor.monitor import Monitor
from kalico_monitor.notifier import deliver
from kalico_monitor.storage import Outbox


def event(seq, kind="jam_detected"):
    boot = "a" * 32
    return {"schema": 1, "boot_id": boot, "seq": seq,
            "event_id": f"{boot}:{seq}", "incident_id": None if seq == 1 else f"{boot}:1",
            "kind": kind, "filename": "synthetic.gcode",
            "window": {"samples": [[0, 12], [.1, 4300]], "duration_s": .1, "sample_count": 2}}


def snapshot(*events):
    return {"filament_force": {"events": list(events)}}


def config(tmp_path):
    return Settings(printer="PEEK", moonraker_url="http://localhost:7125",
                    moonraker_api_key="", hermes_url="http://localhost:8644/direct",
                    hermes_agent_url="http://localhost:8644/agent", hermes_secret="test",
                    sqlite_path=tmp_path / "db", disconnect_seconds=5, stall_minutes=0)


def test_cursor_write_failure_rolls_back_event_and_gap(tmp_path):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    key = "filament_cursor:PEEK:" + "a" * 32
    try:
        tracker = FilamentEventTracker(db)
        tracker.observe(snapshot(event(1)), {})
        with db.conn:
            db.conn.execute("""CREATE TRIGGER injected_failure BEFORE INSERT ON metadata
                WHEN NEW.key LIKE 'filament_cursor:%'
                BEGIN SELECT RAISE(ABORT, 'injected cursor failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected cursor failure"):
            tracker.observe(snapshot(event(4)), {})
        assert db.get_meta(key) == 1
        assert len(db.pending()) == 1
        with db.conn:
            db.conn.execute("DROP TRIGGER injected_failure")
        assert tracker.observe(snapshot(event(4)), {}) == 1
        assert db.get_meta(key) == 4
        assert len(db.pending()) == 3
    finally:
        db.close()


@pytest.mark.asyncio
async def test_route_database_failure_stops_supervised_dispatch(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, db)

    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected database I/O failure")

    monkeypatch.setattr(db, "pending_routes", fail)
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected"):
            await asyncio.wait_for(monitor._dispatch(None), timeout=2)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_malformed_ack_is_classified_as_retryable_value_error():
    response = Mock()
    response.json = AsyncMock(return_value={"status": ["delivered"]})
    context = Mock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    session = Mock()
    session.post.return_value = context
    alert = {"id": "synthetic", "kind": "complete", "severity": "info",
             "filename": "", "detail": "test", "created": 1000.0}
    with pytest.raises(ValueError):
        await deliver(session, "http://localhost:8644/test", "test", "PEEK", alert)


def test_incident_causal_order_precedes_severity(tmp_path):
    db = Outbox(tmp_path / "db", "PEEK")
    try:
        FilamentEventTracker(db).observe(snapshot(event(1), event(2, "jam_confirmed")), {})
        for item in db.unrouted():
            db.ensure_routes(item["id"], ("direct",))
        first = db.pending_routes(route="direct")
        assert [item["kind"] for item in first] == ["filament_jam_detected"]
        db.route_delivered(first[0]["id"], "direct")
        assert [item["kind"] for item in db.pending_routes(route="direct")] == ["filament_jam_confirmed"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_ack_lost_before_db_commit_preserves_request_identity(tmp_path, monkeypatch):
    import kalico_monitor.monitor as module

    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    aid = db.enqueue(Alert("error", 1, "test.gcode", "fault", "critical"))
    db.ensure_routes(aid, ("direct",))
    observed = []
    acknowledged = asyncio.Event()

    async def fake_deliver(_session, _url, _secret, _printer, item, route):
        observed.append(item["id"] + ":" + route)
        acknowledged.set()

    def fail(*_args):
        raise sqlite3.OperationalError("injected ACK checkpoint failure")

    monkeypatch.setattr(module, "deliver", fake_deliver)
    monkeypatch.setattr(db, "route_delivered", fail)
    try:
        with pytest.raises(sqlite3.OperationalError):
            await asyncio.wait_for(Monitor(cfg, db)._deliver_route(None, "direct"), 2)
        assert acknowledged.is_set()
    finally:
        db.close()

    db = Outbox(cfg.sqlite_path, cfg.printer)
    try:
        assert db.pending_routes(route="direct")[0]["id"] == aid
        worker = asyncio.create_task(Monitor(cfg, db)._deliver_route(None, "direct"))
        try:
            for _ in range(50):
                if not db.pending_routes(route="direct"):
                    break
                await asyncio.sleep(.01)
            assert observed == [aid + ":direct", aid + ":direct"]
            assert db.summary()["delivered"] == 1
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    finally:
        db.close()


def test_dead_letter_is_terminal_and_replay_keeps_id(tmp_path):
    db = Outbox(tmp_path / "db", "PEEK")
    try:
        aid = db.enqueue(Alert("error", 1, "", "fault", "critical"))
        db.ensure_routes(aid, ("direct", "agent"))
        db.route_delivered(aid, "direct")
        db.route_dead_letter(aid, "agent", "invalid protocol")
        assert not db.pending_routes()
        assert db.summary()["failed"] == 1
        assert db.summary()["delivered"] == 0
        assert db.dead_letters()[0]["route"] == "agent"
        assert db.retry_dead_letter(aid, "agent")
        assert db.pending_routes()[0]["id"] == aid
        db.route_delivered(aid, "agent")
        assert db.summary()["delivered"] == 1
    finally:
        db.close()


def test_critical_successor_has_bounded_wait(tmp_path):
    import time

    db = Outbox(tmp_path / "db", "PEEK")
    try:
        FilamentEventTracker(db).observe(snapshot(event(1), event(2, "jam_confirmed")), {})
        for item in db.unrouted():
            db.ensure_routes(item["id"], ("direct",))
        first = db.pending_routes(route="direct")[0]
        db.route_failed(first["id"], "direct", "transient")
        assert not db.pending_routes(route="direct")
        assert db.pending_routes(now=time.time()+61, route="direct")[0]["kind"] == "filament_jam_confirmed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_database_executor_does_not_block_loop(tmp_path):
    import time
    from concurrent.futures import ThreadPoolExecutor

    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, db)
    monitor._db_executor = ThreadPoolExecutor(max_workers=1)
    try:
        task = asyncio.create_task(monitor._db_call(time.sleep, .2))
        await asyncio.sleep(.02)
        assert not task.done()
        await task
        await monitor._db_call(db.set_meta, "thread_test", 1)
        assert await monitor._db_call(db.get_meta, "thread_test") == 1
    finally:
        monitor._db_executor.shutdown(wait=True)
        db.close()


def test_communication_health_debounces_and_recovers(tmp_path):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, db)
    try:
        monitor._communication_observation(False, "missing data")
        assert not db.pending()
        monitor._communication_observation(False, "missing data")
        monitor._communication_observation(False, "missing data")
        assert db.get_meta("communication_health")["state"] == "stale"
        assert [a["kind"] for a in db.pending()] == ["status_data_stale"]
        monitor._communication_observation(True, "valid")
        assert [a["kind"] for a in db.pending()] == ["status_data_stale", "status_data_recovered"]
    finally:
        db.close()
