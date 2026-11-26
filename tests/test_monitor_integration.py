"""Simulated Moonraker websocket: no physical printer or heater commands."""
import asyncio

import aiohttp
import pytest
from aiohttp import web

from kalico_monitor.config import Settings
from kalico_monitor.monitor import Monitor
from kalico_monitor.storage import Outbox


@pytest.mark.asyncio
async def test_monitor_receives_complete_and_shutdown(tmp_path):
    active = asyncio.Event()

    async def websocket_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        request_data = await ws.receive_json()
        assert request_data["method"] == "printer.objects.subscribe"
        await ws.send_json({"jsonrpc": "2.0", "id": 1, "result": {"status": {
            "print_stats": {"state": "printing", "filename": "peek.gcode"},
            "webhooks": {"state": "ready", "state_message": ""},
            "virtual_sdcard": {"file_position": 100, "progress": 0.1}}}})
        await ws.send_json({"jsonrpc": "2.0", "method": "notify_status_update", "params": [
            {"print_stats": {"state": "complete"}}, 1.0]})
        active.set()
        await asyncio.sleep(0.12)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/websocket", websocket_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    cfg = Settings(printer="test", moonraker_url=f"http://127.0.0.1:{port}",
        moonraker_api_key="", hermes_url="http://127.0.0.1:8644/webhooks/test",
        hermes_secret="testsecret", sqlite_path=tmp_path / "db.sqlite3",
        disconnect_seconds=5, stall_minutes=0)
    store = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, store)
    async with aiohttp.ClientSession() as session:
        listen = asyncio.create_task(monitor._listen(session))
        await asyncio.wait_for(active.wait(), timeout=1)
        await asyncio.sleep(0.05)
        listen.cancel()
        with pytest.raises(asyncio.CancelledError):
            await listen
    assert store.get_state()["state"] == "complete"
    assert [x["kind"] for x in store.pending()] == ["complete"]
    store.close()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_monitor_shutdown_query_has_actual_reason(tmp_path):
    """Shutdown notification has no payload; fetch webhooks.state_message."""
    seen = asyncio.Event()

    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        request_data = await ws.receive_json()
        assert request_data["id"] == 1
        await ws.send_json({"id": 1, "result": {"status": {
            "print_stats": {"state": "printing", "filename": "peek.gcode"},
            "webhooks": {"state": "ready", "state_message": ""},
            "virtual_sdcard": {"file_position": 42, "progress": 0.01}}}})
        await ws.send_json({"method": "notify_klippy_shutdown", "jsonrpc": "2.0"})
        query = await ws.receive_json()
        assert query["id"] == 2 and query["method"] == "printer.objects.query"
        await ws.send_json({"id": 2, "result": {"status": {
            "print_stats": {"state": "printing", "filename": "peek.gcode"},
            "webhooks": {"state": "shutdown", "state_message": "MCU 'SHT36' shutdown: Thermocouple reader fault"},
            "virtual_sdcard": {"file_position": 42, "progress": 0.01}}}})
        seen.set()
        await asyncio.sleep(0.1)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/websocket", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    cfg = Settings(printer="PEEK-01", moonraker_url=f"http://127.0.0.1:{port}",
        moonraker_api_key="", hermes_url="http://127.0.0.1:8644/webhooks/test",
        hermes_secret="local-test", sqlite_path=tmp_path / "db.sqlite3",
        disconnect_seconds=5, stall_minutes=0)
    store = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, store)
    try:
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(monitor._listen(session))
            await asyncio.wait_for(seen.wait(), timeout=2)
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        pending = store.pending()
        assert len(pending) == 1
        assert pending[0]["kind"] == "klippy_shutdown"
        assert "Thermocouple" in pending[0]["detail"]
    finally:
        store.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_ws_lifecycle_started_paused_resumed_completed(tmp_path):
    """Simulated Moonraker deltas exercise the real subscription and SQLite path."""
    sent = asyncio.Event()

    async def websocket_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        request_data = await ws.receive_json()
        assert request_data["method"] == "printer.objects.subscribe"
        await ws.send_json({"id": 1, "result": {"status": {
            "print_stats": {"state": "standby", "filename": ""},
            "webhooks": {"state": "ready"},
            "virtual_sdcard": {"file_position": 0, "progress": 0.0},
        }}})
        for state in ("printing", "paused", "printing", "paused", "printing", "complete"):
            await ws.send_json({"method": "notify_status_update", "params": [
                {"print_stats": {"state": state, "filename": "peek.gcode"}}, 1.0
            ]})
            await asyncio.sleep(0.01)
        sent.set()
        await asyncio.sleep(0.2)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/websocket", websocket_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    cfg = Settings(printer="PEEK-01", moonraker_url=f"http://127.0.0.1:{port}",
                   moonraker_api_key="", hermes_url="http://127.0.0.1:8644/test",
                   hermes_secret="test", sqlite_path=tmp_path / "db.sqlite",
                   disconnect_seconds=5, stall_minutes=0)
    store = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, store)
    try:
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(monitor._listen(session))
            await asyncio.wait_for(sent.wait(), timeout=2)
            await asyncio.sleep(0.03)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert [row["kind"] for row in store.pending()] == [
            "started", "paused", "resumed", "paused", "resumed", "complete"
        ]
        assert store.get_state()["state"] == "complete"
    finally:
        store.close()
        await runner.cleanup()
