"""Per-event delivery policy. No model is used to select a route."""
from __future__ import annotations

from .event_registry import NATIVE, definition

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
        return modes.get(kind, NATIVE[kind].default_mode) if NATIVE[kind].forwardable else "off"
    return modes.get(kind, modes["default"])


def routes_for(kind: str, modes: dict[str, str]) -> tuple[str, ...]:
    mode = mode_for(kind, modes)
    if mode == "off":
        return ()
    return ("direct", "agent") if mode == "both" else (mode,)


def validate_modes(modes: dict[str, str], rules=()) -> None:
    import warnings

    for kind, mode in modes.items():
        if kind == "default":
            continue
        if kind == "nozzle_force_anomaly":
            if mode != "off":
                raise ValueError("nozzle_force_anomaly 已废弃，请改用 filament_force 事件")
            warnings.warn("nozzle_force_anomaly: off 已废弃，可以删除", UserWarning, stacklevel=2)
            continue
        entry = definition(kind, rules)
        if entry is None:
            raise ValueError(f"未注册事件：{kind}")
        if not entry.forwardable and mode != "off":
            raise ValueError(f"不可转发事件：{kind}")
