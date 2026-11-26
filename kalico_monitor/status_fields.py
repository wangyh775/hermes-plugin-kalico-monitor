"""Selection and formatting rules for zero-LLM /kalico status output."""
from __future__ import annotations

import math

FIELD_LABELS = {
    "state": "打印状态",
    "file": "当前文件",
    "progress": "打印进度",
    "nozzle": "喷嘴温度",
    "bed": "热床温度",
    "chamber": "腔体温度",
    "elapsed": "已打印时间",
    "remaining": "预计剩余时间",
}
DEFAULT_FIELDS = tuple(FIELD_LABELS)
DEFAULT_CONFIG = ",".join(DEFAULT_FIELDS)


def parse_fields(raw: str) -> tuple[str, ...]:
    """Strict, ordered allowlist; reject unknown and duplicate identifiers."""
    result = [part.strip().lower() for part in raw.split(",")]
    if not result or any(not item for item in result):
        raise ValueError("状态字段不能为空或包含空字段")
    if len(result) != len(set(result)):
        raise ValueError("状态字段不能重复")
    invalid = [field for field in result if field not in FIELD_LABELS]
    if invalid:
        raise ValueError(f"不支持的状态字段：{','.join(invalid)}")
    return tuple(result)


def number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def duration(value: object) -> str:
    seconds = number(value)
    if seconds is None or seconds < 0:
        return "—"
    minutes = int(seconds // 60)
    return f"{minutes // 60}小时{minutes % 60}分钟"


def remaining_time(stats: dict, sdcard: dict) -> str:
    """Rough file-position estimate; never claim slicer-quality ETA."""
    if stats.get("state") != "printing":
        return "—（非打印中）"
    elapsed = number(stats.get("print_duration"))
    progress = number(sdcard.get("progress"))
    if (elapsed is None or elapsed <= 0 or progress is None
            or not 0.05 <= progress < 1):
        return "—（数据不足）"
    seconds = elapsed * (1 - progress) / progress
    if seconds < 0 or not math.isfinite(seconds):
        return "—（数据异常）"
    return f"{duration(seconds)}（按文件进度粗略估算）"
