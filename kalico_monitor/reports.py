"""Periodic, no-model print reports, durable across monitor restarts.

Only real printing/ready states are eligible. Pauses and outages reset the
countdown; reports are transactionally queued with their next due timestamp.
"""
from __future__ import annotations

import math
from typing import Any

from .events import Alert
from .storage import Outbox

META_KEY = "periodic_report_v1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _object(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _temperature(value: dict) -> str:
    measured = _number(value.get("temperature"))
    target = _number(value.get("target"))
    current_text = f"{measured:.1f}" if measured is not None else "—"
    target_text = f"{target:.1f}" if target is not None else "—"
    return f"{current_text} / {target_text} °C（实际 / 目标）"


def _duration(seconds: Any) -> str:
    value = _number(seconds)
    if value is None or value < 0:
        return "未知"
    minutes = int(value // 60)
    return f"{minutes // 60} 小时 {minutes % 60} 分钟"


def report_fields(status: dict) -> dict:
    """Structured report data for configurable Direct fields."""
    stats = _object(status, "print_stats")
    sd = _object(status, "virtual_sdcard")
    progress = _number(sd.get("progress"))
    from .status_fields import remaining_time
    layer_info = _object(stats, "info")
    return {
        "progress": f"{progress * 100:.1f}%" if progress is not None and 0 <= progress <= 1 else "—",
        "elapsed": _duration(stats.get("print_duration")),
        "remaining": remaining_time(stats, sd),
        "nozzle": _temperature(_object(status, "extruder")),
        "bed": _temperature(_object(status, "heater_bed")),
        "chamber": _temperature(_object(status, "heater_generic chamber") or _object(status, "temperature_sensor chamber")),
        "layer": _number(layer_info.get("current_layer")),
        "total_layers": _number(layer_info.get("total_layer")),
    }


def report_detail(status: dict) -> str:
    """Format observed Moonraker values only; never infer print quality."""
    stats = _object(status, "print_stats")
    sdcard = _object(status, "virtual_sdcard")
    extruder = _object(status, "extruder")
    bed = _object(status, "heater_bed")
    filename = stats.get("filename")
    filename = filename.strip()[:180] if isinstance(filename, str) else ""
    progress = _number(sdcard.get("progress"))
    percent = f"{progress * 100:.1f}%" if progress is not None and 0 <= progress <= 1 else "未知"
    lines = [
        "【打印过程定时报告】",
        f"文件：{filename or '未知'}",
        "状态：正在打印（Moonraker print_stats）",
        f"进度：{percent}（按文件位置估计）",
        f"已打印：{_duration(stats.get('print_duration'))}",
        f"喷嘴：{_temperature(extruder)}",
        f"热床：{_temperature(bed)}",
    ]
    info = _object(stats, "info")
    layer = _number(info.get("current_layer"))
    total = _number(info.get("total_layer"))
    if layer is not None and total is not None and total > 0 and 0 <= layer <= total:
        lines.append(f"层数：{int(layer)} / {int(total)}")
    return "\n".join(lines)


class PeriodicReporter:
    """Schedule at most one report per interval, using the SQLite outbox."""

    def __init__(self, store: Outbox, interval_minutes: int):
        self.store = store
        self.interval_seconds = max(0, interval_minutes) * 60
        self.tracker = store.get_meta(META_KEY)
        if not isinstance(self.tracker, dict):
            self.tracker = None

    def suspend(self) -> None:
        """Forget an active countdown after transport loss, retaining sequence IDs."""
        if not self.tracker:
            return
        if self.tracker.get("next_due_at") is not None:
            self.tracker = {**self.tracker, "next_due_at": None}
            self.store.report_checkpoint(META_KEY, self.tracker)

    def evaluate(self, state: dict, status: dict, now: float, connected: bool = True) -> bool:
        """Return whether a report was enqueued; no output when disabled."""
        if self.interval_seconds == 0:
            return False
        generation = state.get("generation", 0)
        filename = state.get("filename", "")
        if not isinstance(generation, int) or isinstance(generation, bool):
            return False
        if not isinstance(filename, str):
            return False
        active = (
            connected
            and state.get("state") == "printing"
            and state.get("klippy_state") == "ready"
            and generation > 0
        )
        tracker = self.tracker
        if (tracker is None
                or tracker.get("generation") != generation
                or tracker.get("filename") != filename
                or tracker.get("interval_seconds") != self.interval_seconds):
            tracker = {
                "generation": generation,
                "filename": filename,
                "interval_seconds": self.interval_seconds,
                "sequence": (tracker.get("sequence", 0) if tracker and
                             tracker.get("generation") == generation and
                             tracker.get("filename") == filename else 0),
                "next_due_at": now + self.interval_seconds if active else None,
            }
            self.store.report_checkpoint(META_KEY, tracker)
            self.tracker = tracker
            return False
        if not active:
            if tracker.get("next_due_at") is not None:
                tracker = {**tracker, "next_due_at": None}
                self.store.report_checkpoint(META_KEY, tracker)
                self.tracker = tracker
            return False
        due = _number(tracker.get("next_due_at"))
        if due is None or now < due - self.interval_seconds - 60:
            # Printing resumed, or system clock moved back substantially.
            tracker = {**tracker, "next_due_at": now + self.interval_seconds}
            self.store.report_checkpoint(META_KEY, tracker)
            self.tracker = tracker
            return False
        if now < due:
            return False
        sequence = int(tracker.get("sequence", 0)) + 1
        alert = Alert(
            kind="print_progress",
            generation=generation,
            filename=filename,
            detail=report_detail(status),
            severity="info",
            payload=report_fields(status),
            sequence=sequence, trigger="timer",
        )
        # One SQLite transaction: durable dedupe key + next due + notification.
        updated = {
            **tracker, "sequence": sequence, "next_due_at": now + self.interval_seconds,
        }
        self.store.report_checkpoint(META_KEY, updated, alert)
        self.tracker = updated
        return True
