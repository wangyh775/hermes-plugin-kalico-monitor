"""Fault-injection tests; all remote endpoints are local simulated servers."""

import asyncio
import time

import aiohttp
import pytest
from aiohttp import web

from kalico_monitor.config import Settings
from kalico_monitor.events import Alert, EventProcessor
from kalico_monitor.monitor import Monitor
from kalico_monitor.notifier import deliver
from kalico_monitor.storage import Outbox


def config(tmp_path, port):
    return Settings(
        printer="PEEK-01",
        moonraker_url=f"http://127.0.0.1:{port}",
        moonraker_api_key="",
        hermes_url=f"http://127.0.0.1:{port}/webhooks/kalico-alert",
        hermes_secret="test-secret",
        sqlite_path=tmp_path / "alerts.sqlite3",
        disconnect_seconds=5,
        stall_minutes=0,
    )


def snapshot():
    return {
        "print_stats": {"state": "printing", "filename": "peek.gcode"},
        "webhooks": {"state": "ready", "state_message": ""},
        "virtual_sdcard": {"file_position": 42, "progress": 0.02},
    }


@pytest.mark.asyncio
async def test_shutdown_webhooks_query_preserves_print_context(tmp_path):
    queried = asyncio.Event()

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        assert (await ws.receive_json())["method"] == "printer.objects.subscribe"
        await ws.send_json({"id": 1, "result": {"status": snapshot()}})
        await ws.send_json({"method": "notify_klippy_shutdown", "params": []})
        query = await ws.receive_json()
        assert query["id"] == 2
        assert query["params"]["objects"] == {"webhooks": None}
        await ws.send_json({"id": 2, "result": {"status": {"webhooks": {
            "state": "shutdown",
            "state_message": "MCU 'SHT36' shutdown: Thermocouple reader fault",
        }}}})
        queried.set()
        await asyncio.sleep(0.12)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/websocket", handler)
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
            await asyncio.wait_for(queried.wait(), timeout=2)
            await asyncio.sleep(0.04)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert db.get_state()["filename"] == "peek.gcode"
        alerts = db.pending()
        assert len(alerts) == 1 and alerts[0]["kind"] == "klippy_shutdown"
        assert "SHT36" in alerts[0]["detail"]
    finally:
        db.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_rejected_query_does_not_drop_moonraker_socket(tmp_path):
    stale_sent = asyncio.Event()
    allow_recovery = asyncio.Event()
    finished = asyncio.Event()

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_json()
        await ws.send_json({"id": 1, "result": {"status": snapshot()}})
        await ws.send_json({"method": "notify_klippy_disconnected", "params": []})
        query = await ws.receive_json()
        assert query["method"] == "printer.objects.query"
        await ws.send_json({"id": 2, "error": {"code": 500, "message": "Klippy unavailable"}})
        await ws.send_json({"method": "notify_status_update", "params": [
            {"print_stats": {"state": "complete"}}, 123.0,
        ]})
        stale_sent.set()
        await allow_recovery.wait()
        await ws.send_json({"method": "notify_klippy_ready", "params": []})
        subscribe = await ws.receive_json()
        assert subscribe["method"] == "printer.objects.subscribe"
        state = snapshot()
        state["print_stats"]["state"] = "complete"
        await ws.send_json({"id": subscribe["id"], "result": {"status": state}})
        finished.set()
        await asyncio.sleep(0.15)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/websocket", handler)
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
            await asyncio.wait_for(stale_sent.wait(), timeout=2)
            await asyncio.sleep(0.05)
            assert not task.done(), "Query rejection must not close the socket"
            assert db.get_state()["state"] == "printing"
            assert not monitor.subscription_active
            allow_recovery.set()
            await asyncio.wait_for(finished.wait(), timeout=2)
            await asyncio.sleep(0.05)
            assert monitor.subscription_active
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert db.get_state()["state"] == "complete"
        assert [a["kind"] for a in db.pending()] == ["complete"]
    finally:
        db.close()
        await runner.cleanup()


def test_disconnected_requires_threshold_and_recovers(tmp_path):
    cfg = config(tmp_path, 7125)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    monitor = Monitor(cfg, db)
    processor = EventProcessor()
    processor.set_snapshot(snapshot(), time.time())
    try:
        monitor._klippy_disconnected()
        monitor._tick(processor)
        assert db.pending() == []
        monitor.klippy_down_since -= cfg.disconnect_seconds + 1
        monitor._tick(processor)
        monitor._tick(processor)
        assert [a["kind"] for a in db.pending()] == ["klippy_disconnected"]
        monitor._klippy_recovered()
        monitor._klippy_recovered()
        assert [a["kind"] for a in db.pending()] == [
            "klippy_disconnected", "klippy_recovered",
        ]
    finally:
        db.close()


def test_probe_warning_not_alert_until_job_errors():
    processor = EventProcessor()
    processor.set_snapshot(snapshot(), 100)
    for message in ("Bad probe detected. Retrying", "Probing failed after 7 bad probes"):
        previous, alerts = processor.handle_packet({
            "method": "notify_gcode_response", "params": [message]}, 101)
        assert previous is None and alerts == []
    _, alerts = processor.apply_update({
        "print_stats": {"state": "error", "message": "Probing failed"}}, 102)
    assert len(alerts) == 1 and alerts[0].kind == "error"
    assert "Probing failed after 7 bad probes" in alerts[0].detail


def test_old_job_error_context_is_cleared():
    processor = EventProcessor()
    processor.set_snapshot(snapshot(), 100)
    processor.ingest_gcode_response("Previous job: bad probe")
    processor.apply_update({"print_stats": {"state": "complete"}}, 101)
    processor.apply_update({"print_stats": {"state": "printing"}}, 102)
    _, alerts = processor.apply_update({
        "print_stats": {"state": "error", "message": "New fault"}}, 103)
    assert len(alerts) == 1 and alerts[0].kind == "error"
    assert "Previous job" not in alerts[0].detail


@pytest.mark.asyncio
async def test_retry_webhook_preserves_id_and_pending_event(tmp_path):
    received = []
    first = asyncio.Event()
    second = asyncio.Event()

    async def handler(request):
        received.append(request.headers["X-Request-ID"])
        await request.read()
        if len(received) == 1:
            first.set()
            return web.json_response({"status": "unavailable"}, status=503)
        second.set()
        return web.json_response({"status": "delivered"})

    app = web.Application()
    app.router.add_post("/webhooks/kalico-alert", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    cfg = config(tmp_path, port)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    incident = Alert(kind="klippy_shutdown", generation=1,
                     filename="peek.gcode", detail="SHT36 fault", severity="critical")
    event_id = db.enqueue(incident)
    monitor = Monitor(cfg, db)
    try:
        async with aiohttp.ClientSession() as session:
            sender = asyncio.create_task(monitor._dispatch(session))
            await asyncio.wait_for(first.wait(), timeout=2)
            for _ in range(50):
                await asyncio.sleep(0.01)
                row = db.conn.execute(
                    "SELECT attempts FROM alert_routes WHERE alert_id=? AND route='direct'", (event_id,),
                ).fetchone()
                if row["attempts"] >= 1:
                    break
            assert row["attempts"] >= 1
            assert db.summary()["pending"] == 1
            with db.conn:
                db.conn.execute("UPDATE alert_routes SET next_retry=0 WHERE alert_id=? AND route='direct'", (event_id,))
            await asyncio.wait_for(second.wait(), timeout=4)
            for _ in range(50):
                await asyncio.sleep(0.01)
                if db.summary()["delivered"] == 1:
                    break
            sender.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sender
        assert received == [event_id + ":direct", event_id + ":direct"]
        assert db.summary()["pending"] == 0
        assert db.summary()["delivered"] == 1
    finally:
        db.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_unexpected_webhook_ack_is_rejected(tmp_path):
    async def handler(request):
        return web.json_response(["not-an-acknowledgement"])

    app = web.Application()
    app.router.add_post("/webhooks/kalico-alert", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    cfg = config(tmp_path, port)
    try:
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ValueError, match="did not confirm delivery"):
                await deliver(session, cfg.hermes_url, cfg.hermes_secret, cfg.printer, {
                    "id": "e1", "kind": "complete", "severity": "info",
                    "filename": "peek.gcode", "detail": "done", "created": 1700000000,
                })
    finally:
        await runner.cleanup()
