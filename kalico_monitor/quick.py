"""Deterministic read-only, no-model commands for Hermes gateway chats."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .force import format_force
from .status import query_force_status, query_status, query_status_with_chamber
from .status_fields import (
    DEFAULT_CONFIG,
    chamber_status,
    duration,
    parse_fields,
    remaining_time,
    temperature_values,
)
from .status_fields import number as _number

SUBCOMMANDS = frozenset({"status", "temp", "alerts", "health", "force", "help"})
CONFIG_KEYS = frozenset({
    "MOONRAKER_URL", "MOONRAKER_API_KEY", "KALICO_MONITOR_DB",
    "KALICO_PRINTER_NAME", "KALICO_ALLOWED_CHAT_IDS",
    "KALICO_STATUS_FIELDS", "KALICO_CHAMBER_OBJECT",
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
    from .monitor_cfg import cfg_path, read_cfg
    result.update({k: v for k, v in read_cfg(cfg_path(filename)).items() if k in CONFIG_KEYS})
    return result


def setting(name: str, get_secret: Callable[[str], str] | None = None) -> str:
    # The installer manages this shared file. A stale Hermes plugin secret or
    # process environment must not silently redirect read-only commands to a
    # different printer after --reconfigure.
    shared = shared_config()
    if name in CONFIG_KEYS and name in shared:
        return shared[name]
    if get_secret:
        try:
            value = get_secret(name)
            if value:
                return str(value)
        except (ImportError, RuntimeError, AttributeError):
            pass
    if os.environ.get(name):
        return os.environ[name]
    return shared.get(name, "")


def database_path(get_secret: Callable[[str], str] | None = None) -> Path:
    value = setting("KALICO_MONITOR_DB", get_secret)
    if value:
        return Path(value).expanduser()
    return Path("~/.local/state/kalico-monitor/alerts.sqlite3").expanduser()


def is_allowed(platform: str = "", source: Any = None,
               get_secret: Callable[[str], str] | None = None) -> bool:
    """Hermes authorizes inbound commands; optional chat IDs add a second restriction."""
    if platform in LOCAL_PLATFORMS and source is None:
        return True
    if not platform:
        return False
    chat_id = (source.get("chat_id") if isinstance(source, dict)
               else getattr(source, "chat_id", None))
    allowed = {item.strip() for item in setting("KALICO_ALLOWED_CHAT_IDS", get_secret).split(",")
               if item.strip()}
    if not allowed:
        return True
    return bool(chat_id and str(chat_id) in allowed)


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 92) -> str:
    if not isinstance(value, str):
        return "—"
    return " ".join(value.strip().split())[:limit] or "—"


def _degrees(value: Any) -> str:
    n = _number(value)
    return "—" if n is None else f"{n:.1f}°C"


def format_status(data: dict, printer: str = "Kalico",
                  fields: tuple[str, ...] | None = None) -> str:
    """Format selected fields in declared order; optional fields degrade gracefully."""
    stats = _mapping(data.get("print_stats"))
    hooks = _mapping(data.get("webhooks"))
    virtual = _mapping(data.get("virtual_sdcard"))
    chosen = fields if fields is not None else parse_fields(DEFAULT_CONFIG)
    progress = _number(virtual.get("progress"))
    if progress is None:
        progress_text = "—"
    elif 0 <= progress <= 1:
        progress_text = f"{progress * 100:.1f}%"
    else:
        progress_text = "—（异常值）"
    chamber_temp = _degrees(chamber_status(data).get("temperature"))
    values = {
        "state": f"打印：{_text(stats.get('state'))}｜Klippy：{_text(hooks.get('state'))}",
        "file": f"文件：{_text(stats.get('filename'))}",
        "progress": f"进度：{progress_text}",
        "nozzle": f"喷嘴：{_temperature(data, 'extruder')}",
        "bed": f"热床：{_temperature(data, 'heater_bed')}",
        "chamber": f"腔体：{chamber_temp}",
        "elapsed": f"已打印：{duration(stats.get('print_duration'))}",
        "remaining": f"预计剩余：{remaining_time(stats, virtual)}",
    }
    return "\n".join([f"🖨️ {printer} · 实时状态", *(values[key] for key in chosen)])

def _temperature(status: dict, name: str) -> str:
    measured, target = temperature_values(_mapping(status.get(name)))
    return f"{_degrees(measured)} / 目标 {_degrees(target)}"


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
        health = {r["key"]: json.loads(r["value"]) for r in conn.execute(
            "SELECT key,value FROM metadata WHERE key IN "
            "('subscription_health','transport_connected','native_oversize_count',"
            "'dispatcher_heartbeat_direct','dispatcher_heartbeat_agent','webhook_health_direct',"
            "'webhook_health_agent','storage_health','rpc_health')")}
        # A running old daemon may not have migrated the schema yet.
        has_suppressed = any(row[1] == "suppressed" for row in
                             conn.execute("PRAGMA table_info(alerts)"))
        active = "delivered IS NULL AND suppressed IS NULL" if has_suppressed else "delivered IS NULL"
        count = conn.execute(f"SELECT COUNT(*) FROM alerts WHERE {active}").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        health["dead_letters"] = conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0] if "dead_letters" in tables else 0
        if "alert_routes" in tables:
            health["routes"] = {row["route"]: dict(row) for row in conn.execute("""SELECT route,COUNT(*) AS pending,
                MIN(a.created) AS oldest, MAX(r.attempts) AS max_attempts
                FROM alert_routes r JOIN alerts a ON a.id=r.alert_id
                WHERE r.delivered IS NULL AND r.suppressed IS NULL GROUP BY route""")}
        health["db_bytes"] = sum(p.stat().st_size for p in (path, Path(str(path)+"-wal")) if p.exists())
        select_suppressed = "suppressed" if has_suppressed else "NULL AS suppressed"
        rows = conn.execute(
            "SELECT kind,severity,filename,detail,created,delivered, "
            f"{select_suppressed} FROM alerts WHERE kind != 'print_progress' "
            "ORDER BY created DESC LIMIT ?", (min(max(limit, 1), 10),)
        ).fetchall()
    return {"exists": True, "pending": count, "recent": [dict(r) for r in rows],
            "heartbeat": heartbeat, "health": health}


def format_alerts(data: dict, printer: str = "Kalico") -> str:
    if not data["exists"]:
        return "尚无监控数据库，请确认 Kalico Monitor 服务是否启动。"
    lines = [f"🔔 {printer} · 最近告警（待发送 {data['pending']} 条）"]
    for item in data["recent"]:
        date = time.strftime("%m-%d %H:%M", time.localtime(item["created"]))
        delivery = ("已过滤" if item.get("suppressed") is not None
                    else "待发送" if item["delivered"] is None else "已投递")
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
    health = data.get("health", {})
    subscription = health.get("subscription_health", {})
    transport = health.get("transport_connected")
    transport_text = "已连接" if transport is True else "断开" if transport is False else "未知"
    lines = [f"Moonraker 连接：{transport_text}", f"订阅：{subscription.get('state', 'unknown')}"]
    lines.append(f"Moonraker RPC：{health.get('rpc_health', 'unknown')}")
    lines.append(f"隔离事件/路由：{health.get('dead_letters', 0)}")
    lines.append(f"SQLite 大小：{health.get('db_bytes', 0)/1048576:.1f} MiB")
    if health.get("storage_health", {}).get("pressure"):
        lines.append("⚠ SQLite 容量或磁盘空间达到预警阈值")
    for route, info in health.get("routes", {}).items():
        oldest = max(0, int(now - info["oldest"])) if info.get("oldest") is not None else 0
        lines.append(f"{route} 待发：{info['pending']}，最大重试：{info['max_attempts']}，最早积压：{oldest}s")
    if health.get("native_oversize_count"):
        lines.append(f"原生超限载荷：{health['native_oversize_count']} 条")
    if subscription.get("missing"):
        lines.append("不可用：" + ", ".join(subscription["missing"]))
    for route in ("direct", "agent"):
        stamp = _number(health.get(f"dispatcher_heartbeat_{route}"))
        live = stamp is not None and 0 <= now - stamp <= 90
        lines.append(f"{route} 分发：{'运行中' if live else '未知或过期'}")
        webhook = health.get(f"webhook_health_{route}", {})
        stamp = _number(webhook.get("at"))
        age = max(0, int(now - stamp)) if stamp is not None else None
        status = webhook.get("state", "unknown")
        if age is not None and age > 300:
            status += " (historical)"
        lines.append(f"{route} Webhook：{status} {webhook.get('http_status') or ''}"
                     + (f" · {age}s ago" if age is not None else ""))
    return (f"🩺 {printer} · Monitor 状态\n"
            f"{state}\n待发送告警：{data['pending']} 条\n" + "\n".join(lines) + "\n" +
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
            api_key = setting("MOONRAKER_API_KEY", get_secret)
            if subcommand == "temp":
                return format_temp(query(url, api_key), printer)
            fields = parse_fields(setting("KALICO_STATUS_FIELDS", get_secret)
                                  or DEFAULT_CONFIG)
            if query is query_status:
                data = query_status_with_chamber(
                    url, api_key,
                    chamber=setting("KALICO_CHAMBER_OBJECT", get_secret),
                    include_chamber="chamber" in fields,
                )
            else:
                data = query(url, api_key)
            return format_status(data, printer, fields)
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
