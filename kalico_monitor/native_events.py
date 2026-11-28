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
            serialized = json.dumps(params, ensure_ascii=True, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError, RecursionError, OverflowError):
            return False
        if len(serialized) > MAX_PAYLOAD_BYTES:
            count = self.store.get_meta("native_oversize_count", 0)
            self.store.set_meta("native_oversize_count", count + 1)
            return False
        try:
            json.dumps(params, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except UnicodeError:
            return False
        now = time.monotonic() if now is None else now
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        previous = self.last.get(method)
        if entry.dedup_policy != "none" and previous and previous[0] == fingerprint and now - previous[1] < COOLDOWN_SECONDS:
            return False
        summary = json.dumps(params, ensure_ascii=False, allow_nan=False)[:1000]
        fields, severity = normalized_fields(method, params, entry.severity)
        alert = Alert(
            kind=method, generation=0, filename="", detail=f"{entry.description}: {summary}",
            severity=severity,
            source_id=uuid.uuid4().hex, payload={"params": params, "params_summary": summary, **fields},
            source_method=method, trigger="notification",
        )
        self.store.enqueue(alert)
        self.last[method] = (fingerprint, now)
        return True


def normalized_fields(method, params, severity):
    """Expose bounded, documented scalar fields; retain original positional params."""
    first = params[0] if params else None
    fields = {}
    if method == "notify_service_state_changed" and isinstance(first, dict):
        services = [(name, item) for name, item in first.items()
                    if isinstance(item, dict)]
        fields["service"] = ", ".join(name for name, _ in services)[:300]
        fields["service_state"] = ", ".join(
            f"{name}: {item.get('active_state', '?')}/{item.get('sub_state', '?')}"
            for name, item in services)[:600]
        if any(item.get("active_state") == "failed" for _, item in services):
            severity = "critical"
        elif any(item.get("active_state") == "inactive" for _, item in services):
            severity = "warning"
    elif method == "notify_cpu_throttled" and isinstance(first, dict):
        bits = first.get("bits")
        if type(bits) is int:
            fields["throttle_bits"] = bits
            severity = "warning" if bits & 15 else "info"
    elif method == "notify_gcode_response" and isinstance(first, str):
        fields["gcode_response"] = first[:1000]
        if any(word in first.lower() for word in ("error", "failed", "shutdown")):
            severity = "warning"
    elif isinstance(first, dict):
        for key in ("action", "queue_state", "name"):
            if isinstance(first.get(key), str):
                fields[key] = first[key][:300]
        job = first.get("job")
        if isinstance(job, dict) and isinstance(job.get("filename"), str):
            fields["job_filename"] = job["filename"][:300]
    return fields, severity
