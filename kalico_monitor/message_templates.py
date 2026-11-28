"""Field-selectable, deterministic Direct notifications (no LLM)."""
from __future__ import annotations

import json
import time
from typing import Any

from .custom_config import DEFAULT_MESSAGE_FIELDS
from .event_registry import row_metadata

LABELS = {
    "printer": "设备", "kind": "事件", "severity": "等级", "occurred_at": "时间",
    "filename": "文件", "detail": "详情", "alert_id": "事件 ID",
    "progress": "打印进度", "elapsed": "已打印", "remaining": "预计剩余",
    "nozzle": "喷嘴温度", "bed": "热床温度", "chamber": "腔体温度",
    "layer": "当前层数", "total_layers": "总层数",
    "force_g": "喷嘴力 (gf)", "e_speed_mm_s": "挤出速度 (mm/s)",
    "diagnostic_speed_mm_s": "诊断挤出速度 (mm/s)", "result": "诊断结果",
    "reason": "原因", "step_mm": "单次 Z 补偿 (mm)",
    "total_mm": "累计 Z 补偿 (mm)", "window_summary": "受力窗口",
    "object": "状态对象", "field": "监测字段", "value": "当前值",
    "threshold": "阈值", "source_type": "来源类型", "source": "来源",
    "source_method": "原生通知方法", "source_object": "来源对象", "trigger": "触发路径",
}


def payload_dict(alert: dict) -> dict[str, Any]:
    raw = alert.get("payload")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    if not isinstance(raw, dict):
        return {}
    return raw


def render_direct_message(alert: dict, printer: str,
                          fields: tuple[str, ...] = DEFAULT_MESSAGE_FIELDS) -> str:
    event = payload_dict(alert)
    if isinstance(event.get("event"), dict):
        event = event["event"]
    basic = {
        "printer": printer,
        "kind": alert.get("kind"),
        "severity": alert.get("severity"),
        "occurred_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                     time.localtime(alert.get("created", time.time()))),
        "filename": alert.get("filename"),
        "detail": alert.get("detail"),
        "alert_id": alert.get("id"),
    }
    if isinstance(event.get("window"), dict):
        window = event["window"]
        event = {**event, "window_summary": (
            f"{window.get('duration_s', '—')} s / "
            f"{window.get('sample_count', '—')} 原始点"
        )}
    values = {**event, **basic, **row_metadata(alert)}
    # Hermes DIRECT_PROMPT owns the static heading; avoid two titles.
    lines = []
    for field in fields:
        value = values.get(field)
        if value is None or value == "":
            value = "—"
        elif not isinstance(value, (str, int, float, bool)):
            value = "—"
        lines.append(f"{LABELS[field]}：{value}")
    return "\n".join(lines)
