"""Hermes generic V2 HMAC Webhook; send without running a model."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import aiohttp

from .custom_config import DEFAULT_MESSAGE_FIELDS
from .event_registry import row_metadata, utc_timestamp
from .message_templates import render_direct_message


def make_request(alert: dict, secret: str, printer: str, timestamp: int | None = None,
                 route: str = "direct", message_fields: dict | None = None,
                 message_templates: dict | None = None) -> tuple[bytes, dict[str, str]]:
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
    payload.update(row_metadata(alert))
    # Preserve legacy occurred_at (queue time); do not invent source time.
    payload.update(queued_at=utc_timestamp(alert['created']),
                   received_at=payload.get('received_at'), source_occurred_at=None)
    if alert.get("payload"):
        payload["event"] = json.loads(alert["payload"])
    if route == "direct":
        options = message_fields or {}
        selected = options.get(alert["kind"], options.get("default", DEFAULT_MESSAGE_FIELDS))
        templates = message_templates or {}
        kind = alert["kind"]
        template = templates.get(kind)
        if template is None and kind not in options:
            template = templates.get("default")
        payload["message"] = render_direct_message(alert, printer, selected, template)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), str(timestamp).encode() + b"." + body,
                         hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": str(timestamp),
        "X-Webhook-Signature-V2": signature,
        "X-Request-ID": alert["id"] + (":agent" if route == "agent" else ":direct"),
    }


async def deliver(session: aiohttp.ClientSession, url: str, secret: str,
                  printer: str, alert: dict, route: str = "direct",
                  message_fields: dict | None = None,
                  message_templates: dict | None = None) -> dict[str, Any]:
    body, headers = make_request(alert, secret, printer, route=route,
                                 message_fields=message_fields, message_templates=message_templates)
    async with session.post(url, data=body, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)) as response:
        response.raise_for_status()
        result = await response.json()
    accepted = {"accepted", "duplicate"} if route == "agent" else {"delivered", "duplicate"}
    if (not isinstance(result, dict) or not isinstance(result.get("status"), str)
            or result["status"] not in accepted):
        status = result.get("status") if isinstance(result, dict) else type(result).__name__
        raise ValueError(f"Webhook did not confirm delivery: {status}")
    return result
