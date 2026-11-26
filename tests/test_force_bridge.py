"""Custom Klippy PR #13 force/rebound status: read-only, edge-triggered, resilient."""
import asyncio
import time

import aiohttp
import pytest
from aiohttp import web

from kalico_monitor.config import Settings
from kalico_monitor.events import EventProcessor
from kalico_monitor.force import ForceEventTracker, alert_detail, format_force
from kalico_monitor.monitor import SUBSCRIBE, Monitor, subscribe_request
from kalico_monitor.quick import execute
from kalico_monitor.storage import Outbox


def force_state(count=0, triggered=False, action="pause"):
    return {
        "enabled": True, "triggered": triggered, "trigger_count": count,
        "current_layer": 12, "force_g": 87.3, "baseline_g": 55.0,
        "delta_g": -32.3, "last_trigger_force_g": -120.0,
        "last_trigger_delta_g": -175.0, "threshold_g": 100.0,
        "matching_events": 3 if triggered else 1, "required_events": 3,
        "window_s": 60.0, "action": action, "sample_errors": 0,
    }


def job(state="printing", generation=1):
    return {"state": state, "filename": "PEEK.gcode",
            "generation": generation, "klippy_state": "ready"}


def snapshot(force=None, print_state="printing"):
    s = {"print_stats": {"state": print_state, "filename": "PEEK.gcode"},
         "webhooks": {"state": "ready"},
         "virtual_sdcard": {"file_position": 12, "progress": 0.12}}
    if force is not None:
        s["nozzle_force_monitor"] = force
    return s


def config(tmp_path, port=7125, watch_force=True):
    return Settings(
        printer="PEEK-01", moonraker_url=f"http://127.0.0.1:{port}",
        moonraker_api_key="", hermes_url="http://127.0.0.1:8644/webhooks/test",
        hermes_secret="test-secret", sqlite_path=tmp_path / "alerts.sqlite3",
        disconnect_seconds=5, stall_minutes=0, watch_nozzle_force=watch_force,
    )


def test_force_message_uses_latest_pr13_window_algorithm():
    text = format_force({"nozzle_force_monitor": force_state(1, True)}, "PEEK-01")
    assert "3/3" in text and "60 秒" in text
    assert "-32.3 gf" in text
    assert "自动暂停" not in text
    assert "未发现 nozzle_force_monitor" in format_force({})
    detail = alert_detail({"nozzle_force_monitor": force_state(1, True)}, job())
    assert "疑似堵头，非确诊" in detail
    assert "尚未确认打印实际暂停" in detail
    assert "-175.0 gf" in detail


def test_force_readonly_slash_uses_specialized_query_and_no_llm(monkeypatch):
    monkeypatch.setenv("KALICO_PRINTER_NAME", "PEEK-01")
    def forbidden(*_args):
        raise AssertionError("must not query the base printer for /kalico force")
    def force_query(_url, _key):
        return {"nozzle_force_monitor": force_state(1, True)}
    result = execute("force", query=forbidden, force_query=force_query)
    assert "PEEK-01" in result
    assert "累计触发：1" in result
    assert "3/3" in result
    assert "未发现" in execute("force", force_query=lambda *_: {})


def test_no_event_on_baseline_then_one_latched_alarm(tmp_path):
    store = Outbox(tmp_path / "alerts.sqlite3", "PEEK-01")
    tracker = ForceEventTracker(store)
    try:
        assert not tracker.observe({"nozzle_force_monitor": force_state()}, job(), now=100)
        assert tracker.observe({"nozzle_force_monitor": force_state(1, True)}, job(), now=101)
        assert len(store.pending()) == 1
        msg = store.pending()[0]
        assert msg["kind"] == "nozzle_force_anomaly"
        assert msg["severity"] == "warning"
        assert "3/3" in msg["detail"]
        assert not tracker.observe({"nozzle_force_monitor": force_state(1, True)}, job(), 102)
        assert tracker.suppress_generic_pause(job("paused"), now=104)
        assert not tracker.suppress_generic_pause(job("paused"), now=150)
        assert len(store.pending()) == 1
    finally:
        store.close()


def test_monitor_restart_no_duplicate_and_next_trigger_unique(tmp_path):
    db_path = tmp_path / "alerts.sqlite3"
    store = Outbox(db_path, "PEEK-01")
    try:
        tracker = ForceEventTracker(store)
        assert tracker.observe({"nozzle_force_monitor": force_state(1, True)}, job(), 100)
        first_id = store.pending()[0]["id"]
        tracker = ForceEventTracker(store)
        assert not tracker.observe({"nozzle_force_monitor": force_state(1, True)}, job(), 101)
        assert not tracker.observe({"nozzle_force_monitor": force_state(1, False)}, job(), 102)
        assert tracker.observe({"nozzle_force_monitor": force_state(2, True)}, job(), 103)
        assert len(store.pending()) == 2
        assert first_id != store.pending()[1]["id"]
        # New job with unchanged latched status is not another event.
        assert not tracker.observe({"nozzle_force_monitor": force_state(2, True)}, job(
            generation=2), 104)
        assert len(store.pending()) == 2
    finally:
        store.close()


def test_klippy_counter_reset_is_new_epoch_and_no_missing_object_alarm(tmp_path):
    store = Outbox(tmp_path / "alerts.sqlite3", "PEEK-01")
    tracker = ForceEventTracker(store)
    try:
        assert not tracker.observe({}, job(), 100)
        assert not tracker.observe({"nozzle_force_monitor": force_state(0, False)}, job(), 101)
        assert tracker.observe({"nozzle_force_monitor": force_state(4, True)}, job(), 102)
        assert not tracker.observe({"nozzle_force_monitor": force_state(0, False)}, job(), 103)
        assert tracker.observe({"nozzle_force_monitor": force_state(1, True)}, job(), 104)
        assert len(store.pending()) == 2
    finally:
        store.close()


def test_malformed_force_fields_never_alert(tmp_path):
    store = Outbox(tmp_path / "alerts.sqlite3", "PEEK-01")
    tracker = ForceEventTracker(store)
    try:
        for value in (None, "1", True, -1, 1.5):
            assert not tracker.observe({
                "nozzle_force_monitor": {"trigger_count": value, "triggered": True}
            }, job(), 100)
        assert not tracker.observe({
            "nozzle_force_monitor": {"trigger_count": 2, "triggered": "true"}
        }, job(), 101)
        assert store.pending() == []
    finally:
        store.close()


def test_subscribe_optional_object_only_when_enabled(monkeypatch):
    normal = subscribe_request(False)
    extra = subscribe_request(True)
    assert "nozzle_force_monitor" not in normal["params"]["objects"]
    assert extra["params"]["objects"]["nozzle_force_monitor"] is None
    assert "nozzle_force_monitor" not in SUBSCRIBE["params"]["objects"]
    monkeypatch.setenv("HERMES_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("KALICO_WATCH_NOZZLE_FORCE", "1")
    assert Settings.load().watch_nozzle_force is True
    monkeypatch.setenv("KALICO_WATCH_NOZZLE_FORCE", "garbage")
    with pytest.raises(ValueError):
        Settings.load()


def test_normal_manual_pause_is_preserved(tmp_path):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, db)
    processor = EventProcessor()
    try:
        monitor._process_snapshot(processor, snapshot(force_state()))
        state, events = processor.apply_update({"print_stats": {"state": "paused"}}, time.time())
        monitor._record(processor, state, events)
        assert [item["kind"] for item in db.pending()] == ["paused"]
    finally:
        db.close()


def test_force_pause_same_delta_preserves_generic_pause(tmp_path):
    cfg = config(tmp_path)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, db)
    processor = EventProcessor()
    try:
        monitor._process_snapshot(processor, snapshot(force_state()))
        state, alerts = processor.apply_update({
            "nozzle_force_monitor": force_state(1, True),
            "print_stats": {"state": "paused"},
        }, time.time())
        monitor._record(processor, state, alerts)
        assert [item["kind"] for item in db.pending()] == ["nozzle_force_anomaly", "paused"]
        assert "已观察到打印暂停" in db.pending()[0]["detail"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_missing_optional_klippy_object_falls_back_to_standard_subscription(tmp_path):
    got_fallback = asyncio.Event()

    async def websocket_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        first = await ws.receive_json()
        assert first["method"] == "printer.objects.subscribe"
        assert "nozzle_force_monitor" in first["params"]["objects"]
        await ws.send_json({"id": 1, "error": {"code": 404, "message": "object unavailable"}})
        second = await ws.receive_json()
        assert second["method"] == "printer.objects.subscribe"
        assert "nozzle_force_monitor" not in second["params"]["objects"]
        await ws.send_json({"id": 1, "result": {"status": snapshot()}})
        got_fallback.set()
        await asyncio.sleep(0.15)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/websocket", websocket_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    cfg = config(tmp_path, port)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, db)
    try:
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(monitor._listen(session))
            await asyncio.wait_for(got_fallback.wait(), timeout=2)
            for _ in range(50):
                if db.get_state() is not None:
                    break
                await asyncio.sleep(0.01)
            assert db.get_state() is not None
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert db.get_state()["filename"] == "PEEK.gcode"
        assert db.pending() == []
    finally:
        db.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ws_force_trigger_delivers_event_and_pause(tmp_path):
    triggered = asyncio.Event()

    async def websocket_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        first = await ws.receive_json()
        assert "nozzle_force_monitor" in first["params"]["objects"]
        await ws.send_json({"id": 1, "result": {"status": snapshot(force_state())}})
        await ws.send_json({"method": "notify_status_update", "params": [{
            "nozzle_force_monitor": force_state(1, True),
            "print_stats": {"state": "paused"},
        }, 23.0]})
        triggered.set()
        await asyncio.sleep(0.15)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/websocket", websocket_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    cfg = config(tmp_path, port)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, db)
    try:
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(monitor._listen(session))
            await asyncio.wait_for(triggered.wait(), timeout=2)
            await asyncio.sleep(0.04)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert [a["kind"] for a in db.pending()] == ["nozzle_force_anomaly", "paused"]
    finally:
        db.close()
        await runner.cleanup()
