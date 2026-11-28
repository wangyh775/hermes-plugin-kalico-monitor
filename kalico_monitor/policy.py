"""Per-event delivery policy. No model is used to select a route."""
from __future__ import annotations

from .event_registry import NATIVE

MODES = frozenset({"direct", "agent", "both"})
EVENT_MODES = MODES | {"off"}
DEFAULTS = {}


def parse_modes(value: str) -> dict[str, str]:
    """Comma-separated kind=mode overrides, with default=direct."""
    result = {"default": "direct", **DEFAULTS}
    for entry in filter(None, (x.strip() for x in value.split(","))):
        if "=" not in entry:
            raise ValueError("KALICO_EVENT_MODES must use kind=off|direct|agent|both")
        kind, mode = (x.strip() for x in entry.split("=", 1))
        if not kind or not all(c.isalnum() or c == "_" for c in kind) or mode not in EVENT_MODES:
            raise ValueError("Invalid KALICO_EVENT_MODES entry")
        result[kind] = mode
    return result


def mode_for(kind: str, modes: dict[str, str]) -> str:
    if kind in NATIVE:
        return modes.get(kind, "off") if NATIVE[kind].forwardable else "off"
    return modes.get(kind, modes["default"])


def routes_for(kind: str, modes: dict[str, str]) -> tuple[str, ...]:
    mode = mode_for(kind, modes)
    if mode == "off":
        return ()
    return ("direct", "agent") if mode == "both" else (mode,)
