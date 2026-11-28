"""Opt-in Moonraker notification adapter; bounded, read-only, best effort."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from .event_registry import NATIVE
from .events import Alert
from .policy import routes_for

MAX_PAYLOAD_BYTES = 16384
COOLDOWN_SECONDS = 2


class NativeEventAdapter:
    def __init__(self, store, modes):
        self.store = store
        self.modes = modes
        self.last = {}

    def observe(self, packet: dict, now: float | None = None) -> bool:
        method = packet.get("method")
        if not isinstance(method, str) or "id" in packet:
            return False
        entry = NATIVE.get(method)
        if not entry or not entry.forwardable or not routes_for(method, self.modes):
            return False
        params = packet.get("params", [])
        if not isinstance(params, list):
            return False
        try:
            serialized = json.dumps(params, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError, RecursionError, OverflowError):
            return False
        if len(serialized) > MAX_PAYLOAD_BYTES:
            return False
        try:
            json.dumps(params, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except UnicodeError:
            return False
        now = time.monotonic() if now is None else now
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        previous = self.last.get(method)
        if previous and previous[0] == fingerprint and now - previous[1] < COOLDOWN_SECONDS:
            return False
        summary = json.dumps(params, ensure_ascii=False, allow_nan=False)[:1000]
        alert = Alert(
            kind=method, generation=0, filename="", detail=f"{entry.description}: {summary}",
            severity=entry.severity,
            source_id=uuid.uuid4().hex, payload={"params": params, "params_summary": summary},
            source_method=method, trigger="notification",
        )
        self.store.enqueue(alert)
        self.last[method] = (fingerprint, now)
        return True
