"""Field-selectable, deterministic Direct notifications (no LLM)."""
from __future__ import annotations

import json
import time
from typing import Any

from .custom_config import DEFAULT_MESSAGE_FIELDS, PLACEHOLDER
from .event_registry import row_metadata, utc_timestamp

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
    "received_at": "接收时间", "queued_at": "入队时间", "source_occurred_at": "源事件时间",
    "previous_state": "原状态", "current_state": "当前状态", "klippy_state": "Klippy 状态",
    "params_summary": "原始参数摘要", "service": "服务", "service_state": "服务状态",
    "throttle_bits": "限频位", "gcode_response": "G-code 响应", "action": "动作",
    "queue_state": "队列状态", "name": "名称", "job_filename": "任务文件",
    "incident_id": "故障关联 ID", "source_event_id": "源事件 ID", "generation": "任务代次",
    "sequence": "事件序号", "incident_key": "故障关联键",
    "payload_degraded": "载荷降级", "source_eventtime": "源单调时钟", "source_clock_id": "源时钟域", "generated_at": "生成时间",
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
                          fields: tuple[str, ...] = DEFAULT_MESSAGE_FIELDS,
                          template: str | None = None) -> str:
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
    values = {**event, **basic, **row_metadata(alert),
              "queued_at": utc_timestamp(alert.get("created", time.time())),
              "source_occurred_at": None}
    # Hermes DIRECT_PROMPT owns the static heading; avoid two titles.
    def scalar(field):
        value = values.get(field)
        if value is None or value == "" or not isinstance(value, (str, int, float, bool)):
            return "—"
        return str(value)

    if template is not None:
        return PLACEHOLDER.sub(lambda match: scalar(match.group(1)), template)[:12000]
    lines = [f"{LABELS[field]}：{scalar(field)}" for field in fields]
    return "\n".join(lines)
