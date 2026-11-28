"""Opt-in Moonraker notification adapter; bounded, read-only, best effort."""
from __future__ import annotations

import json
import time
import uuid

from .event_registry import NATIVE
from .events import Alert
from .policy import routes_for

MAX_PAYLOAD_BYTES = 16384
COOLDOWN_SECONDS = 30


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
        if method in self.last and now - self.last[method] < COOLDOWN_SECONDS:
            return False
        alert = Alert(
            kind=method, generation=0, filename="", detail=entry.description,
            severity="warning" if method == "notify_cpu_throttled" else "info",
            source_id=uuid.uuid4().hex, payload={"params": params},
            source_method=method, trigger="notification",
        )
        self.store.enqueue(alert)
        self.last[method] = now
        return True
