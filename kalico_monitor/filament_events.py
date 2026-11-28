"""Read-only bridge for the filament_force Klippy status event journal."""
from __future__ import annotations

import json
import math
from typing import Any

from .event_registry import BUILTINS
from .events import Alert
from .storage import Outbox

OBJECT = "filament_force"
EVENTS = {
    kind.removeprefix("filament_"): (entry.description, entry.severity)
    for kind, entry in BUILTINS.items() if entry.source_type == "klipper_extension"
}


def finite(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except OverflowError:
        return None


def valid_event(event: Any) -> bool:
    if not isinstance(event, dict) or event.get("schema") != 1:
        return False
    boot, seq = event.get("boot_id"), event.get("seq")
    if (not isinstance(boot, str) or len(boot) != 32
        or any(c not in "0123456789abcdef" for c in boot)
        or type(seq) is not int or not 0 < seq < 2**63
        or event.get("event_id") != f"{boot}:{seq}"
        or not isinstance(event.get("kind"), str)
        or event["kind"] not in EVENTS):
        return False
    window = event.get("window")
    if event["kind"] in ("jam_detected", "runout_detected") and not isinstance(window, dict):
        return False
    if window is not None:
        if not isinstance(window, dict):
            return False
        samples = window.get("samples")
        if not isinstance(samples, list) or len(samples) > 2048:
            return False
        if any(not isinstance(row, list) or len(row) != 2
               or any(finite(x) is None for x in row) for row in samples):
            return False
    try:
        return len(json.dumps(event, allow_nan=False, ensure_ascii=False).encode('utf-8')) <= 256_000
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        return False


def detail(event: dict) -> str:
    title, _ = EVENTS[event["kind"]]
    lines = [title, f"事件 ID：{event['event_id']}"]
    for key, label, unit in (
        ("source", "检测来源", ""), ("layer", "层数", ""),
        ("force_g", "触发受力", " gf"), ("e_speed_mm_s", "触发 E 速度", " mm/s"),
        ("diagnostic_speed_mm_s", "诊断 E 速度", " mm/s"),
        ("result", "诊断结果", ""), ("reason", "失败原因", ""),
        ("step_mm", "单次 Z 补偿", " mm"), ("total_mm", "累计 Z 补偿", " mm"),
    ):
        value = event.get(key)
        if isinstance(value, str) or finite(value) is not None:
            lines.append(f"{label}：{str(value)[:100]}{unit}")
    window = event.get("window")
    if isinstance(window, dict):
        lines.append(f"受力窗口：{window.get('duration_s', 0)} s，"
                     f"{window.get('sample_count', 0)} 原始点，"
                     f"{len(window.get('samples', []))} 传输点")
    return "\n".join(lines)[:1500]


class FilamentEventTracker:
    def __init__(self, store: Outbox):
        self.store = store

    def observe(self, status: dict, state: dict, *, source_method=None) -> int:
        obj = status.get(OBJECT)
        events = obj.get("events") if isinstance(obj, dict) else None
        if not isinstance(events, list):
            return 0
        count = 0
        for event in events[-16:]:
            if not valid_event(event):
                continue
            kind = event["kind"]
            filename = event.get("filename") or state.get("filename", "")
            filename = filename[:180] if isinstance(filename, str) else ""
            generation = state.get("generation", 0)
            if type(generation) is not int:
                generation = 0
            alert = Alert(kind="filament_" + kind, generation=generation,
                          filename=filename, detail=detail(event),
                          severity=EVENTS[kind][1], source_id=event["event_id"],
                          payload=event, trigger="extension_journal", source_method=source_method)
            count += int(self.store.enqueue_source(alert))
        return count
