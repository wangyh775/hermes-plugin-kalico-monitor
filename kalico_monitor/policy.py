"""Per-event delivery policy. No model is used to select a route."""
from __future__ import annotations

MODES = frozenset({"direct", "agent", "both"})
DEFAULTS = {}


def parse_modes(value: str) -> dict[str, str]:
    """Comma-separated kind=mode overrides, with default=direct."""
    result = {"default": "direct", **DEFAULTS}
    for entry in filter(None, (x.strip() for x in value.split(","))):
        if "=" not in entry:
            raise ValueError("KALICO_EVENT_MODES must use kind=direct|agent|both")
        kind, mode = (x.strip() for x in entry.split("=", 1))
        if not kind or not all(c.isalnum() or c == "_" for c in kind) or mode not in MODES:
            raise ValueError("Invalid KALICO_EVENT_MODES entry")
        result[kind] = mode
    return result


def routes_for(kind: str, modes: dict[str, str]) -> tuple[str, ...]:
    mode = modes.get(kind, modes["default"])
    return ("direct", "agent") if mode == "both" else (mode,)
