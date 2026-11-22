"""Deterministic read-only, no-model commands for Hermes gateway chats."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .force import format_force
from .status import query_force_status, query_status

SUBCOMMANDS = frozenset({"status", "temp", "alerts", "health", "force", "help"})
CONFIG_KEYS = frozenset({
    "MOONRAKER_URL", "MOONRAKER_API_KEY", "KALICO_MONITOR_DB",
    "KALICO_PRINTER_NAME", "KALICO_ALLOWED_CHAT_IDS",
})
HELP = "用法：/kalico [status|temp|alerts|health|force|help]（仅查询，不控制打印机）"
LOCAL_PLATFORMS = frozenset({"cli", "tui", "desktop", "local"})


def shared_config(path: str | Path | None = None) -> dict[str, str]:
    """Read only known keys; NEVER source or execute a shell environment file."""
    filename = Path(path or os.environ.get(
        "KALICO_MONITOR_ENV_FILE", "~/.config/kalico-monitor/monitor.env"
    )).expanduser()
    try:
        lines = filename.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    result: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() not in CONFIG_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key.strip()] = value
    return result


def setting(name: str, get_secret: Callable[[str], str] | None = None) -> str:
    if get_secret:
        try:
            value = get_secret(name)
            if value:
                return str(value)
        except (ImportError, RuntimeError, AttributeError):
            pass
    if os.environ.get(name):
        return os.environ[name]
    return shared_config().get(name, "")


def database_path(get_secret: Callable[[str], str] | None = None) -> Path:
    value = setting("KALICO_MONITOR_DB", get_secret)
    if value:
        return Path(value).expanduser()
    return Path("~/.local/state/kalico-monitor/alerts.sqlite3").expanduser()


def is_allowed(platform: str = "", source: Any = None,
               get_secret: Callable[[str], str] | None = None) -> bool:
    """Nonlocal gateways are denied unless the chat id is explicitly allowed."""
    if platform in LOCAL_PLATFORMS and source is None:
        return True
    chat_id = (source.get("chat_id") if isinstance(source, dict)
               else getattr(source, "chat_id", None))
    allowed = {item.strip() for item in setting("KALICO_ALLOWED_CHAT_IDS", get_secret).split(",")
               if item.strip()}
    return bool(chat_id and str(chat_id) in allowed)


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 92) -> str:
    if not isinstance(value, str):
        return "—"
    return " ".join(value.strip().split())[:limit] or "—"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _degrees(value: Any) -> str:
    n = _number(value)
    return "—" if n is None else f"{n:.1f}°C"


def format_status(data: dict, printer: str = "Kalico") -> str:
    stats = _mapping(data.get("print_stats"))
    hooks = _mapping(data.get("webhooks"))
    virtual = _mapping(data.get("virtual_sdcard"))
    state = _text(stats.get("state"))
    klippy = _text(hooks.get("state"))
    filename = _text(stats.get("filename"))
    progress = _number(virtual.get("progress"))
    if progress is None:
        progress_text = "—"
    elif 0 <= progress <= 1:
        progress_text = f"{progress * 100:.1f}%"
    else:
        progress_text = "—（异常值）"
    return (f"🖨️ {printer} · 实时状态\n"
            f"打印：{state}｜Klippy：{klippy}\n"
            f"文件：{filename}\n进度：{progress_text}\n"
            f"喷嘴：{_temperature(data, 'extruder')}\n"
            f"热床：{_temperature(data, 'heater_bed')}")


def _temperature(status: dict, name: str) -> str:
    obj = _mapping(status.get(name))
    return f"{_degrees(obj.get('temperature'))} / 目标 {_degrees(obj.get('target'))}"


def format_temp(data: dict, printer: str = "Kalico") -> str:
    hooks = _mapping(data.get("webhooks"))
    return (f"🌡️ {printer} · 实时温度\n"
            f"喷嘴：{_temperature(data, 'extruder')}\n"
            f"热床：{_temperature(data, 'heater_bed')}\n"
            f"Klippy：{_text(hooks.get('state'))}")


def read_monitor(path: Path, limit: int = 5) -> dict:
    """Read the same SQLite DB in read-only mode, without mutating or creating it."""
    if not path.is_file():
        return {"exists": False, "pending": 0, "recent": [], "heartbeat": None}
    uri = path.absolute().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=1) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM metadata WHERE key='last_heartbeat_at'").fetchone()
        heartbeat = None if row is None else json.loads(row["value"])
        count = conn.execute("SELECT COUNT(*) FROM alerts WHERE delivered IS NULL").fetchone()[0]
        rows = conn.execute(
            "SELECT kind,severity,filename,detail,created,delivered "
            "FROM alerts WHERE kind != 'print_progress' ORDER BY created DESC LIMIT ?", (min(max(limit, 1), 10),)
        ).fetchall()
    return {"exists": True, "pending": count, "recent": [dict(r) for r in rows],
            "heartbeat": heartbeat}


def format_alerts(data: dict, printer: str = "Kalico") -> str:
    if not data["exists"]:
        return "尚无监控数据库，请确认 Kalico Monitor 服务是否启动。"
    lines = [f"🔔 {printer} · 最近告警（待发送 {data['pending']} 条）"]
    for item in data["recent"]:
        date = time.strftime("%m-%d %H:%M", time.localtime(item["created"]))
        delivery = "待发送" if item["delivered"] is None else "已投递"
        lines.append(f"{date} [{_text(item['severity'], 12)}] "
                     f"{_text(item['kind'], 28)} · {delivery}\n"
                     f"  {_text(item['detail'], 160)}")
    if not data["recent"]:
        lines.append("暂无告警记录。")
    return "\n".join(lines)


def format_health(data: dict, printer: str = "Kalico", now: float | None = None) -> str:
    if not data["exists"]:
        return "⚪ 监控数据库未创建，无法确认服务状态。"
    now = time.time() if now is None else now
    heartbeat = _number(data["heartbeat"])
    if heartbeat is None:
        state = "未知（暂无心跳）"
    elif 0 <= now - heartbeat <= 90:
        state = f"最近心跳 {int(now - heartbeat)} 秒前"
    else:
        state = "心跳过期（监听服务可能中断）"
    return (f"🩺 {printer} · Monitor 状态\n"
            f"{state}\n待发送告警：{data['pending']} 条\n"
            "注：心跳反映监控进程，不代表飞书通知已验证送达。")


def execute(raw_args: str, *, get_secret: Callable[[str], str] | None = None,
            query: Callable[[str, str], dict] = query_status,
            force_query: Callable[[str, str], dict] = query_force_status,
            now: float | None = None) -> str:
    parts = (raw_args or "").lower().strip().split()
    if len(parts) > 1 or (parts and parts[0] not in SUBCOMMANDS):
        return HELP
    subcommand = parts[0] if parts else "status"
    if subcommand == "help":
        return HELP
    printer = setting("KALICO_PRINTER_NAME", get_secret) or "Kalico"
    try:
        if subcommand in ("status", "temp"):
            url = setting("MOONRAKER_URL", get_secret) or "http://127.0.0.1:7125"
            data = query(url, setting("MOONRAKER_API_KEY", get_secret))
            return (format_status(data, printer) if subcommand == "status"
                    else format_temp(data, printer))
        if subcommand == "force":
            url = setting("MOONRAKER_URL", get_secret) or "http://127.0.0.1:7125"
            data = force_query(url, setting("MOONRAKER_API_KEY", get_secret))
            return format_force(data, printer)
        data = read_monitor(database_path(get_secret))
        return (format_alerts(data, printer) if subcommand == "alerts"
                else format_health(data, printer, now))
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError, TypeError):
        # No API keys, filesystem paths, or raw tracebacks in public chat replies.
        return "查询失败：Moonraker 不可达，或 Monitor 数据库不可读。请在主机查看服务日志。"
