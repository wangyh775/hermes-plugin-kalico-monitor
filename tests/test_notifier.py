import hashlib
import hmac
import json

from kalico_monitor.notifier import make_request


def test_generic_v2_signature_and_stable_delivery_id():
    alert = {"id": "d7bc", "kind": "klippy_shutdown", "severity": "critical",
             "filename": "peek.gcode", "detail": "Thermocouple reader fault",
             "created": 1700000000}
    body, headers = make_request(alert, "shared-secret", "PEEK-01", timestamp=1700000001)
    expected = hmac.new(b"shared-secret", b"1700000001." + body, hashlib.sha256).hexdigest()
    assert headers["X-Webhook-Signature-V2"] == expected
    assert headers["X-Webhook-Timestamp"] == "1700000001"
    assert headers["X-Request-ID"] == "d7bc:direct"
    assert json.loads(body)["printer"] == "PEEK-01"


async def _fake_delivery_success():
    import aiohttp
    from aiohttp import web

    from kalico_monitor.notifier import deliver
    received = {}

    async def handler(request):
        received["header"] = request.headers["X-Webhook-Signature-V2"]
        received["body"] = await request.read()
        return web.json_response({"status": "delivered", "route": "kalico-alert"})

    app = web.Application()
    app.router.add_post("/webhooks/kalico-alert", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            result = await deliver(session, f"http://127.0.0.1:{port}/webhooks/kalico-alert",
                                   "test-secret", "PEEK-01", {
                "id": "eid1", "kind": "complete", "severity": "info",
                "filename": "test.gcode", "detail": "done", "created": 1700000000,
            })
        assert result["status"] == "delivered"
        assert json.loads(received["body"])["kind"] == "complete"
        assert len(received["header"]) == 64
    finally:
        await runner.cleanup()


def test_local_hermes_webhook_delivery():
    import asyncio
    asyncio.run(_fake_delivery_success())
