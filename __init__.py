"""Hermes plugin entrypoint (read-only tools). Monitor runs separately via systemd."""
from __future__ import annotations

import json
import os
from pathlib import Path


def register(ctx) -> None:
    if __package__:
        from .kalico_monitor.status import query_status
        from .kalico_monitor.storage import Outbox
    else:
        from kalico_monitor.status import query_status
        from kalico_monitor.storage import Outbox

    def _env_secret(name):
        try:
            from agent.secret_scope import get_secret
            return get_secret(name) or os.environ.get(name, "")
        except ImportError:
            return os.environ.get(name, "")

    def _status(_args=None, **_kwargs):
        try:
            status = query_status(_env_secret("MOONRAKER_URL") or "http://127.0.0.1:7125",
                                  _env_secret("MOONRAKER_API_KEY"))
            return json.dumps({"status": status}, ensure_ascii=False)
        except (OSError, ValueError, RuntimeError) as exc:
            return json.dumps({"ok": False, "error_type": type(exc).__name__}, ensure_ascii=False)

    def _health(_args=None, **_kwargs):
        path = Path(os.environ.get("KALICO_MONITOR_DB", str(
            Path.home() / ".local/state/kalico-monitor/alerts.sqlite3")))
        if not path.exists():
            return json.dumps({"ok": False, "message": "Monitor database not yet created"})
        db = Outbox(path, os.environ.get("KALICO_PRINTER_NAME", "PEEK-01"))
        try:
            return json.dumps(db.summary(), ensure_ascii=False)
        finally:
            db.close()

    ctx.register_tool(
        name="kalico_status", toolset="kalico_monitor", handler=_status,
        emoji="🖨️", schema={"name": "kalico_status",
            "description": "Read-only query: printer state and temperatures from Moonraker; no G-code or motion commands.",
            "parameters": {"type": "object", "properties": {}}},
    )
    ctx.register_tool(
        name="kalico_monitor_health", toolset="kalico_monitor", handler=_health,
        emoji="🔔", schema={"name": "kalico_monitor_health",
            "description": "Inspect local Kalico monitor alert queue, recent alerts and last print state; read only.",
            "parameters": {"type": "object", "properties": {}}},
    )
