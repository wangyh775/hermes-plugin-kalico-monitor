"""Read-only bridge for the optional Kalico PR #13 nozzle rebound detector.

Kalico alone decides whether to pause.  The Hermes monitor only observes a
Klippy get_status() object and persists one notification per trigger event.
"""
from __future__ import annotations

import math
import time
from typing import Any

from .events import Alert
from .storage import Outbox

OBJECT = "nozzle_force_monitor"
META_KEY = "nozzle_force_bridge_v1"


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _force(value: Any) -> str:
    number = _number(value)
    return f"{number:+.1f} gf" if number is not None else "未知"


def _plain_number(value: Any) -> str:
    number = _number(value)
    return f"{number:.1f} gf" if number is not None else "未知"


def format_force(data: dict, printer: str = "Kalico") -> str:
    """Format only actual, available status values; avoid predicting a clog."""
    force = _dict(data.get(OBJECT))
    if not force:
        return ("未发现 nozzle_force_monitor 状态对象。"
                "请确认 Kalico PR #13 已安装、printer.cfg 已添加"
                " [nozzle_force_monitor]，并已重启固件。")
    enabled = force.get("enabled")
    triggered = force.get("triggered")
    layer = _nonnegative_int(force.get("current_layer"))
    count = _nonnegative_int(force.get("trigger_count"))
    matched = _nonnegative_int(force.get("matching_events"))
    required = _nonnegative_int(force.get("required_events"))
    window = _number(force.get("window_s"))
    action = force.get("action")
    lines = [
        f"🧰 {printer} · 喷嘴力回弹监控",
        f"检测：{'启用' if enabled is True else '关闭' if enabled is False else '未知'}"
        f"｜异常锁存：{'是' if triggered is True else '否' if triggered is False else '未知'}",
        f"层数：{layer if layer is not None else '未知'}",
        f"当前力：{_plain_number(force.get('force_g'))}",
        f"基线力：{_plain_number(force.get('baseline_g'))}",
        f"力偏差：{_force(force.get('delta_g'))}",
        f"阈值：{_plain_number(force.get('threshold_g'))}",
        f"相似回弹：{matched if matched is not None else '未知'}"
        f"/{required if required is not None else '未知'}"
        f"（窗口 {window:g} 秒）" if window is not None else
        f"相似回弹：{matched if matched is not None else '未知'}"
        f"/{required if required is not None else '未知'}",
        f"累计触发：{count if count is not None else '未知'}",
        f"检测策略：{action if action in ('pause', 'log') else '未知'}",
    ]
    return "\n".join(lines)


def alert_detail(status: dict, state: dict) -> str:
    """No claim that clog is proven or that PAUSE successfully completed."""
    force = _dict(status.get(OBJECT))
    matched = _nonnegative_int(force.get("matching_events"))
    required = _nonnegative_int(force.get("required_events"))
    window = _number(force.get("window_s"))
    layer = _nonnegative_int(force.get("current_layer"))
    action = force.get("action")
    pausing = action == "pause"
    pause_state = state.get("state") == "paused"
    if pausing and pause_state:
        action_text = "已观察到打印暂停；暂停原因仍需结合日志核实"
    elif pausing:
        action_text = "Kalico 已配置请求 PAUSE；尚未确认打印实际暂停"
    else:
        action_text = "仅记录异常；未启用本模块自动暂停"
    detail = [
        "【喷嘴力回弹异常（疑似堵头，非确诊）】",
        f"打印文件：{str(state.get('filename', '') or '未知')[:180]}",
        f"当前层数：{layer if layer is not None else '未知'}",
        f"回弹匹配：{matched if matched is not None else '未知'}"
        f"/{required if required is not None else '未知'}",
        f"检测窗口：{f'{window:g} 秒' if window is not None else '未知'}",
        f"触发时喷嘴力：{_plain_number(force.get('last_trigger_force_g'))}",
        f"触发时力偏差：{_force(force.get('last_trigger_delta_g'))}",
        f"阈值：{_plain_number(force.get('threshold_g'))}",
        f"处理状态：{action_text}",
    ]
    return "\n".join(detail)


class ForceEventTracker:
    """Durable counter/edge recognition; one SQLite transaction per new alarm."""

    def __init__(self, store: Outbox):
        self.store = store
        old = store.get_meta(META_KEY)
        self.tracker = old if isinstance(old, dict) else None

    def observe(self, status: dict, state: dict, now: float | None = None) -> bool:
        force = status.get(OBJECT)
        if not isinstance(force, dict):
            return False
        count = _nonnegative_int(force.get("trigger_count"))
        triggered = force.get("triggered")
        if count is None or not isinstance(triggered, bool):
            return False
        now = time.time() if now is None else now
        prev = self.tracker
        before = prev.get("trigger_count", 0) if prev is not None else 0
        previous_latch = prev.get("triggered", False) if prev is not None else False
        new_alarm = triggered and count > 0 and (
            prev is None or count != before or not previous_latch
        )
        seq = int(prev.get("sequence", 0)) if prev else 0
        next_state = {
            "trigger_count": count,
            "triggered": triggered,
            "sequence": seq + int(new_alarm),
            "last_alert_at": prev.get("last_alert_at") if prev else None,
            "last_alert_generation": prev.get("last_alert_generation") if prev else None,
            "last_alert_filename": prev.get("last_alert_filename") if prev else None,
            "last_alert_action": prev.get("last_alert_action") if prev else None,
        }
        alert = None
        if new_alarm:
            generation = state.get("generation", 0)
            generation = generation if type(generation) is int and generation >= 0 else 0
            filename = state.get("filename", "")
            filename = filename if isinstance(filename, str) else ""
            alert = Alert(
                kind="nozzle_force_anomaly",
                generation=generation,
                filename=filename,
                detail=alert_detail(status, state),
                severity="warning",
                sequence=seq + 1,
            )
            next_state.update(
                last_alert_at=now,
                last_alert_generation=generation,
                last_alert_filename=filename,
                last_alert_action=force.get("action"),
            )
        if next_state != prev:
            self.store.report_checkpoint(META_KEY, next_state, alert)
            self.tracker = next_state
        return new_alarm

    def suppress_generic_pause(self, state: dict, now: float | None = None) -> bool:
        """Avoid duplicate pause notification immediately after force alarm."""
        current = self.tracker
        if not current or current.get("last_alert_action") != "pause":
            return False
        now = time.time() if now is None else now
        eventtime = _number(current.get("last_alert_at"))
        generation = state.get("generation")
        filename = state.get("filename")
        return bool(
            eventtime is not None and 0 <= now - eventtime <= 15
            and generation == current.get("last_alert_generation")
            and filename == current.get("last_alert_filename")
        )
