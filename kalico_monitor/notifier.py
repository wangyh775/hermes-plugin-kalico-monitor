"""Hermes generic V2 HMAC Webhook; send without running a model."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import aiohttp


def make_request(alert: dict, secret: str, printer: str, timestamp: int | None = None, route: str = "direct") -> tuple[bytes, dict[str, str]]:
    if timestamp is None:
        timestamp = int(time.time())
    body = json.dumps({
        "printer": printer,
        "kind": alert["kind"],
        "severity": alert["severity"],
        "filename": alert["filename"],
        "detail": alert["detail"],
        "occurred_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(alert["created"])),
        "alert_id": alert["id"],
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), str(timestamp).encode() + b"." + body,
                         hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": str(timestamp),
        "X-Webhook-Signature-V2": signature,
        "X-Request-ID": alert["id"] + (":agent" if route == "agent" else ":direct"),
    }


async def deliver(session: aiohttp.ClientSession, url: str, secret: str,
                  printer: str, alert: dict, route: str = "direct") -> dict[str, Any]:
    body, headers = make_request(alert, secret, printer, route=route)
    async with session.post(url, data=body, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)) as response:
        response.raise_for_status()
        result = await response.json()
    accepted = {"accepted", "duplicate"} if route == "agent" else {"delivered", "duplicate"}
    if not isinstance(result, dict) or result.get("status") not in accepted:
        status = result.get("status") if isinstance(result, dict) else type(result).__name__
        raise ValueError(f"Webhook did not confirm delivery: {status}")
    return result
