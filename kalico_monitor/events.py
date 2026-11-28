"""Moonraker JSON-RPC packet parsing and stateful alert classification."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from .event_registry import BUILTINS
from .watch import compare, sample_from_status


@dataclass(frozen=True)
class Alert:
    kind: str
    generation: int
    filename: str
    detail: str
    severity: str
    sequence: int | None = None
    source_id: str | None = None
    payload: dict | None = None
    source_method: str | None = None
    trigger: str | None = None

    @property
    def id_key(self) -> str:
        if self.source_id is not None:
            return f"{self.kind}:{self.source_id}"
        base = f"{self.kind}:{self.generation}:{self.filename}"
        return base if self.sequence is None else f"{base}:{self.sequence}"


SEVERITY = {name: entry.severity for name, entry in BUILTINS.items()}


class EventProcessor:
    def __init__(self, previous: dict | None = None, stall_minutes: int = 0):
        self.previous = previous
        self.status: dict[str, dict] = {}
        self.stall_minutes = max(0, stall_minutes)
        self.recent_errors: deque[str] = deque(maxlen=8)
        self.has_snapshot = False

    def _alerts(self, events: list[dict], sample: dict,
                source_method: str | None, trigger: str | None,
                previous: dict | None) -> list[Alert]:
        out = []
        for event in events:
            detail = event["detail"].strip()
            if event["kind"] in ("error", "klippy_shutdown") and self.recent_errors:
                detail += " | Recent G-code: " + " / ".join(list(self.recent_errors)[-3:])
            from .reports import report_fields
            payload = {**report_fields(self.status),
                       "previous_state": (previous or {}).get("state"),
                       "current_state": sample["state"],
                       "klippy_state": sample["klippy_state"]}
            out.append(Alert(kind=event["kind"], generation=event["generation"],
                             filename=sample["filename"], detail=detail[:1500],
                             severity=SEVERITY[event["kind"]],
                             sequence=event.get("sequence"), payload=payload,
                             source_method=source_method if event["kind"] != "stalled" else None,
                             trigger=trigger if event["kind"] != "stalled" else "timer"))
        return out

    def set_snapshot(self, status: dict, now: float, *, source_method=None, trigger="snapshot") -> tuple[dict, list[Alert]]:
        """A subscription reply is a full snapshot; reconcile with persisted state."""
        self.status = {key: dict(value) for key, value in status.items() if isinstance(value, dict)}
        self.has_snapshot = True
        return self.evaluate(now, source_method=source_method, trigger=trigger, allow_stall=False)

    def apply_update(self, update: dict, now: float, *, source_method=None, trigger="state_update") -> tuple[dict, list[Alert]]:
        """Changes are deltas, not complete objects. Merge fields in-place."""
        if not self.has_snapshot:
            return self.previous or {}, []
        for key, delta in update.items():
            if isinstance(delta, dict):
                self.status.setdefault(key, {}).update(delta)
        return self.evaluate(now, source_method=source_method, trigger=trigger, allow_stall=False)

    def evaluate(self, now: float, *, source_method=None, trigger="timer", allow_stall=True) -> tuple[dict, list[Alert]]:
        sample = sample_from_status(self.status)
        old_generation = self.previous.get("generation", 0) if self.previous else 0
        previous = self.previous
        current, events = compare(previous, sample, now, self.stall_minutes, allow_stall=allow_stall)
        # Error messages are job-specific: a new print must not inherit G-code
        # errors from the preceding task.
        if current["generation"] > old_generation:
            self.recent_errors.clear()
        self.previous = current
        return current, self._alerts(events, sample, source_method, trigger, previous)

    def ingest_gcode_response(self, message: Any) -> None:
        if not isinstance(message, str):
            return
        lowered = message.lower()
        if any(x in lowered for x in ("error", "failed", "shutdown", "bad probe")):
            self.recent_errors.append(message.strip()[:300])

    def handle_packet(self, packet: dict, now: float) -> tuple[dict | None, list[Alert]]:
        """Parse notifications. Shutdown event itself is followed by a full query.

        Notify_klippy_shutdown has no payload; callers must query state_message
        rather than making up a reason. This function only handles status deltas.
        """
        method = packet.get("method")
        params = packet.get("params", [])
        if not isinstance(params, list):
            return None, []
        if method == "notify_gcode_response" and params:
            self.ingest_gcode_response(params[0])
        if method == "notify_status_update" and params and isinstance(params[0], dict):
            return self.apply_update(params[0], now, source_method=method)
        return None, []
