"""Hermes read-only tools and zero-LLM /kalico gateway commands.

The realtime Moonraker listener is a separately supervised systemd process.
"""
from __future__ import annotations

import asyncio
import json


def register(ctx) -> None:
    if __package__:
        from .kalico_monitor.quick import database_path, execute, is_allowed, read_monitor, setting
        from .kalico_monitor.status import query_force_status, query_status
    else:
        from kalico_monitor.quick import database_path, execute, is_allowed, read_monitor, setting
        from kalico_monitor.status import query_force_status, query_status

    def _secret(name: str) -> str:
        try:
            from agent.secret_scope import get_secret
            return get_secret(name) or ""
        except ImportError:
            return ""

    def _status(_args=None, **_kwargs):
        try:
            status = query_status(
                setting("MOONRAKER_URL", _secret) or "http://127.0.0.1:7125",
                setting("MOONRAKER_API_KEY", _secret),
            )
            return json.dumps({"status": status}, ensure_ascii=False)
        except (OSError, ValueError, RuntimeError):
            return json.dumps({"ok": False, "error": "moonraker_unavailable"})

    def _force(_args=None, **_kwargs):
        try:
            status = query_force_status(
                setting("MOONRAKER_URL", _secret) or "http://127.0.0.1:7125",
                setting("MOONRAKER_API_KEY", _secret),
            )
            return json.dumps({"status": status}, ensure_ascii=False)
        except (OSError, ValueError, RuntimeError):
            return json.dumps({"ok": False, "error": "nozzle_force_unavailable"})

    def _health(_args=None, **_kwargs):
        try:
            report = read_monitor(database_path(_secret))
            return json.dumps(report, ensure_ascii=False)
        except (OSError, ValueError, RuntimeError):
            return json.dumps({"ok": False, "error": "monitor_database_unavailable"})

    async def _slash(raw_args: str, *, platform: str = "", source=None, **_context) -> str:
        # Chat identity comes from Hermes gateway metadata, never raw arguments.
        if not is_allowed(platform, source, _secret):
            return "无权查询：请将此飞书会话 ID 添加到 KALICO_ALLOWED_CHAT_IDS。"
        # Keep blocking Moonraker HTTP and SQLite outside Hermes gateway loop.
        return await asyncio.to_thread(execute, raw_args, get_secret=_secret)

    ctx.register_command(
        "kalico", handler=_slash,
        description="零模型查询 Kalico：状态、温度、喷嘴力、告警、监控健康",
        args_hint="[status|temp|force|alerts|health|help]",
    )
    ctx.register_tool(
        name="kalico_status", toolset="kalico_monitor", handler=_status,
        emoji="🖨️", schema={"name": "kalico_status",
            "description": "Read-only Moonraker print and temperature status; no printer control.",
            "parameters": {"type": "object", "properties": {}}},
    )
    ctx.register_tool(
        name="kalico_monitor_health", toolset="kalico_monitor", handler=_health,
        emoji="🔔", schema={"name": "kalico_monitor_health",
            "description": "Read-only monitor heartbeat, recent alerts and outbox queue.",
            "parameters": {"type": "object", "properties": {}}},
    )

    ctx.register_tool(
        name="kalico_force", toolset="kalico_monitor", handler=_force,
        emoji="🧰", schema={"name": "kalico_force",
            "description": "Read-only query of custom Klippy nozzle force and rebound detector status.",
            "parameters": {"type": "object", "properties": {}}},
    )
