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
    payload = {
        "printer": printer,
        "kind": alert["kind"],
        "severity": alert["severity"],
        "filename": alert["filename"],
        "detail": alert["detail"],
        "occurred_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(alert["created"])),
        "alert_id": alert["id"],
    }
    if alert.get("payload"):
        payload["event"] = json.loads(alert["payload"])
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), str(timestamp).encode() + b"." + body,
                         hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": str(timestamp),
        "X-Webhook-Signature-V2": signature,
        "X-Request-ID": alert["id"] + (":agent" if route == "agent" else ":direct"),
    }


class PermanentDeliveryError(ValueError):
    """Configuration, payload or protocol error requiring operator review."""


async def deliver(session: aiohttp.ClientSession, url: str, secret: str,
                  printer: str, alert: dict, route: str = "direct") -> dict[str, Any]:
    try:
        body, headers = make_request(alert, secret, printer, route=route)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise PermanentDeliveryError("invalid stored event payload") from exc
    async with session.post(url, data=body, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)) as response:
        try:
            response.raise_for_status()
        except aiohttp.ClientResponseError as exc:
            if 400 <= exc.status < 500 and exc.status not in (408, 409, 425, 429):
                raise PermanentDeliveryError(f"HTTP {exc.status}; check route configuration") from exc
            raise
        try:
            result = await response.json()
        except (ValueError, aiohttp.ContentTypeError) as exc:
            raise PermanentDeliveryError("invalid webhook JSON acknowledgement") from exc
    accepted = {"accepted", "duplicate"} if route == "agent" else {"delivered", "duplicate"}
    status = result.get("status") if isinstance(result, dict) else None
    if not isinstance(status, str) or status not in accepted:
        raise PermanentDeliveryError("Webhook did not confirm delivery: invalid acknowledgement")
    return result
