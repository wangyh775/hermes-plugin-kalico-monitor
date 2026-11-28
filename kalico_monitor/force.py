"""Read-only compatibility view of the current filament_force plugin status."""
from __future__ import annotations

from .filament_events import EVENTS

OBJECT = "filament_force"


def format_force(data: dict, printer: str = "Kalico") -> str:
    status = data.get(OBJECT) if isinstance(data, dict) else None
    if not isinstance(status, dict):
        return ("未发现 filament_force 状态对象。请确认已安装 klipper-filament-force，"
                "且 Kalico 已加载 [filament_force]。")
    events = status.get("events")
    events = events if isinstance(events, list) else []
    lines = [f"🧰 {printer} · filament_force 监控",
             f"最近记录事件：{len(events)}"]
    if events:
        last = events[-1]
        if isinstance(last, dict):
            kind = last.get("kind")
            lines.extend([
                f"最近事件：{EVENTS.get(kind, (kind,))[0]}",
                f"事件 ID：{last.get('event_id', '—')}",
                f"当前受力：{last.get('force_g', '—')} gf",
                f"当前层数：{last.get('layer', '—')}",
            ])
    return "\n".join(lines)
