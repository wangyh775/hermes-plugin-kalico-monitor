"""Canonical event definitions; independent of transport and routing policy."""
from __future__ import annotations

import json
from dataclasses import dataclass

SOURCE_TYPES = (
    "moonraker_native", "moonraker_lifecycle", "klipper_extension",
    "monitor_internal", "custom_rule", "external",
)


@dataclass(frozen=True)
class EventDefinition:
    kind: str
    source_type: str
    source: str
    description: str
    source_object: str | None = None
    native_method: str | None = None
    explicit_only: bool = False
    forwardable: bool = True


def _group(names, source_type, source, source_object=None):
    return {
        kind: EventDefinition(kind, source_type, source, title, source_object)
        for kind, title in names.items()
    }


BUILTINS = {
    **_group({
        "started": "开始打印", "paused": "暂停打印", "resumed": "恢复打印",
        "complete": "打印完成", "cancelled": "取消打印", "error": "打印异常",
    }, "moonraker_lifecycle", "moonraker", "print_stats"),
    **_group({
        "klippy_shutdown": "Klippy 停机", "klippy_disconnected": "Klippy 断开",
        "klippy_recovered": "Klippy 恢复",
    }, "moonraker_native", "moonraker", "webhooks"),
    **_group({
        "stalled": "打印停滞", "print_progress": "定时进度",
        "moonraker_unreachable": "Moonraker 断开", "moonraker_recovered": "Moonraker 恢复",
    }, "monitor_internal", "monitor"),
    **_group({
        "filament_jam_detected": "疑似堵头", "filament_runout_detected": "断料或送料异常",
        "filament_diagnosis_result": "二次诊断", "filament_jam_confirmed": "确认堵头",
        "filament_z_adjusted": "首层 Z 补偿", "filament_recovery_started": "恢复开始",
        "filament_recovery_verified": "恢复验证通过", "filament_recovery_failed": "恢复失败",
    }, "klipper_extension", "filament_force", "filament_force"),
}

# Catalog is a documented API reference, not a claim of live server support.
# Raw high-frequency, authentication and arbitrary agent payloads are catalog-only.
_FORWARD = {
    "notify_filelist_changed": "文件变化",
    "notify_history_changed": "任务历史变化",
    "notify_job_queue_changed": "任务队列变化",
    "notify_service_state_changed": "系统服务状态变化",
    "notify_cpu_throttled": "CPU 限频状态变化",
    "notify_button_event": "按钮事件",
    "notify_active_spool_set": "当前料卷变化",
    "notify_spoolman_status_changed": "Spoolman 状态变化",
    "notify_webcams_changed": "摄像头配置变化",
    "notify_gcode_response": "G-code 响应（限频）",
}
_CATALOG_ONLY = {
    "notify_status_update": "订阅状态更新（内部消费）",
    "notify_klippy_ready": "Klippy 就绪（内部消费）",
    "notify_klippy_shutdown": "Klippy 停机（内部消费）",
    "notify_klippy_disconnected": "Klippy 断连（内部消费）",
    "notify_update_response": "软件更新响应",
    "notify_update_refreshed": "软件版本刷新",
    "notify_proc_stat_update": "系统统计（高频）",
    "notify_user_created": "用户创建",
    "notify_user_deleted": "用户删除",
    "notify_user_logged_out": "用户退出",
    "notify_announcement_update": "公告更新",
    "notify_announcement_dismissed": "公告关闭",
    "notify_announcement_wake": "公告唤醒",
    "notify_sudo_alert": "权限请求",
    "notify_agent_event": "Agent 事件",
    "notify_sensor_update": "传感器更新",
    "notify_power_changed": "电源设备变化（依版本）",
}
NATIVE = {
    method: EventDefinition(
        method, "moonraker_native", "moonraker", title,
        native_method=method, explicit_only=True, forwardable=method in _FORWARD,
    )
    for method, title in {**_FORWARD, **_CATALOG_ONLY}.items()
}
REGISTRY = {**BUILTINS, **NATIVE}


def definition(kind: str, rules=()) -> EventDefinition | None:
    if kind in REGISTRY:
        return REGISTRY[kind]
    for rule in rules:
        if rule.name == kind:
            return EventDefinition(kind, "custom_rule", "moonraker", "自定义数值规则", rule.object)
    return None


def metadata(kind: str, payload=None, observed_method=None, trigger=None) -> dict:
    """Describe origin separately from the actual observed trigger.

    Old rows lack provenance; infer only the category, never an observed method.
    """
    entry = REGISTRY.get(kind)
    if entry is None and isinstance(payload, dict) and payload.get("source") == "moonraker":
        entry = EventDefinition(kind, "custom_rule", "moonraker", "", payload.get("object"))
    return {
        "schema_version": 1,
        "source_type": entry.source_type if entry else None,
        "source": entry.source if entry else None,
        "source_object": entry.source_object if entry else None,
        "source_method": observed_method,
        "trigger": trigger,
    }


def row_metadata(row: dict) -> dict:
    raw = row.get("origin")
    if raw:
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return value
        except (TypeError, ValueError):
            pass
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    return metadata(row["kind"], payload)
