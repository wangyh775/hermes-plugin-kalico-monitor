"""Optional INI-style configuration for Monitor-only behavior and event routes.

monitor.cfg is not printer.cfg: it is read by the independent Jetson service.
Connection credentials and HMAC secrets deliberately remain in monitor.env.
"""
from __future__ import annotations

import configparser
import os
import re
import tempfile
from pathlib import Path

from .event_registry import BUILTINS, NATIVE
from .policy import EVENT_MODES, mode_for, parse_modes

MONITOR_OPTIONS = {
    "progress_report_minutes": "KALICO_PROGRESS_REPORT_MINUTES",
    "stall_minutes": "KALICO_STALL_MINUTES",
    "disconnect_seconds": "KALICO_DISCONNECT_SECONDS",
    "watch_nozzle_force": "KALICO_WATCH_NOZZLE_FORCE",
}
PRINTER_OPTIONS = {"name": "KALICO_PRINTER_NAME", "moonraker_url": "MOONRAKER_URL"}
STATUS_OPTIONS = {"fields": "KALICO_STATUS_FIELDS", "chamber_object": "KALICO_CHAMBER_OBJECT"}
HERMES_OPTIONS = {"direct_chat_id": "KALICO_DIRECT_CHAT_ID", "agent_chat_id": "KALICO_AGENT_CHAT_ID"}
CFG_KEYS = frozenset((*MONITOR_OPTIONS.values(), "KALICO_EVENT_MODES"))
# Compatibility alias for older imports; registry is the canonical definition.
LIFECYCLE_EVENTS = tuple(BUILTINS)
_VALID_KIND = re.compile(r"^[A-Za-z0-9_]+$")


def cfg_path(env_path: Path | None = None, values: dict[str, str] | None = None) -> Path:
    """Use explicit env override, or a cfg next to the service EnvironmentFile."""
    explicit = ((values or {}).get("KALICO_MONITOR_CFG") or
                os.environ.get("KALICO_MONITOR_CFG", ""))
    return (Path(explicit).expanduser() if explicit else
            (env_path or Path("~/.config/kalico-monitor/monitor.env").expanduser()).with_suffix(".cfg"))


def _parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True, delimiters=(":", "="))
    parser.optionxform = str
    return parser


def read_cfg(path: Path) -> dict[str, str]:
    """Parse only the documented sections; never execute shell or G-code."""
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ValueError(f"拒绝读取符号链接形式的 Monitor CFG：{path}")
    parser = _parser()
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (configparser.Error, UnicodeError) as exc:
        raise ValueError(f"Monitor CFG 格式错误：{path}: {exc}") from exc
    if parser.defaults():
        raise ValueError("monitor.cfg 不支持 [DEFAULT] 节")
    from .custom_config import load_extensions
    load_extensions(path)  # Validate extensible message and event sections.

    from .setup import validate_field  # Deferred to keep the parser import-light.
    values: dict[str, str] = {}
    if parser.has_section("monitor"):
        for option, raw in parser.items("monitor"):
            if option not in MONITOR_OPTIONS:
                raise ValueError(f"未知 [monitor] 配置项：{option}")
            env_key = MONITOR_OPTIONS[option]
            values[env_key] = validate_field(env_key, raw)
    for section, options in (("printer", PRINTER_OPTIONS), ("status", STATUS_OPTIONS)):
        if parser.has_section(section):
            for option, raw in parser.items(section):
                if option not in options:
                    raise ValueError(f"未知 [{section}] 配置项：{option}")
                values[options[option]] = validate_field(options[option], raw)
    if parser.has_section("hermes"):
        for option, raw in parser.items("hermes"):
            if option not in HERMES_OPTIONS:
                raise ValueError(f"未知 [hermes] 配置项：{option}")
            values[HERMES_OPTIONS[option]] = validate_field(HERMES_OPTIONS[option], raw)
    if parser.has_section("events"):
        pairs = []
        for kind, mode in parser.items("events"):
            if not _VALID_KIND.fullmatch(kind) or mode not in EVENT_MODES:
                raise ValueError(f"无效事件策略：{kind}={mode}（仅支持 off/direct/agent/both）")
            pairs.append(f"{kind}={mode}")
        value = ",".join(pairs)
        parse_modes(value)
        values["KALICO_EVENT_MODES"] = value
    return values


def render_cfg(values: dict[str, str]) -> str:
    """Validate first, then produce a deterministic, secret-free configuration."""
    from .setup import validate_field
    lines = [
        "# Kalico Monitor standalone-service configuration (not printer.cfg).",
        "# Credentials and Hermes webhook settings remain in monitor.env.",
        "[monitor]",
    ]
    for option, key in MONITOR_OPTIONS.items():
        lines.append(f"{option}: {validate_field(key, values[key])}")
    for section, options in (("printer", PRINTER_OPTIONS), ("status", STATUS_OPTIONS)):
        lines.extend(["", f"[{section}]"])
        for option, key in options.items():
            lines.append(f"{option}: {validate_field(key, values[key])}")
    lines.extend(["", "[hermes]"])
    for option, key in HERMES_OPTIONS.items():
        lines.append(f"{option}: {validate_field(key, values.get(key, ''))}")
    lines.extend(["", "[events]", "# default applies to unlisted events."])
    raw = values.get("KALICO_EVENT_MODES", "")
    if raw:
        validated = parse_modes(raw)
        ordered = ["default"] + [name for name in LIFECYCLE_EVENTS if name in validated]
        ordered += [name for name in validated if name not in ordered]
    else:
        validated = parse_modes("")
        # Preserve the inheritance rule: no implicit per-event overrides.
        ordered = ["default"]
    for kind in ordered:
        lines.append(f"{kind}: {validated.get(kind, validated['default'])}")
    lines.append("")
    return "\n".join(lines)


def save_cfg(path: Path, values: dict[str, str]) -> None:
    """Atomic 0600 replacement, failing closed on symlinks or invalid values."""
    from .custom_config import preserved_extension_text
    from .custom_config import load_extensions
    from .policy import validate_modes

    _, rules = load_extensions(path)
    validate_modes(parse_modes(values.get("KALICO_EVENT_MODES", "")), rules)
    content = render_cfg(values) + preserved_extension_text(path)
    if path.is_symlink():
        raise ValueError(f"拒绝覆盖符号链接形式的 Monitor CFG：{path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".monitor-cfg-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def edit_events(current: str, ask=input, out=print) -> str:
    """Interactive per-event mode editor; no file modifications until confirmed."""
    policies = parse_modes(current)
    original = current
    events = list(LIFECYCLE_EVENTS) + [name for name, item in NATIVE.items() if item.forwardable]
    for name in policies:
        if name not in events and name != "default":
            events.append(name)
    while True:
        out("\n事件路由 (off=关闭 / direct=直推飞书 / agent=模型分析 / both=两路)")
        out(f"[d] 默认策略: {policies['default']}")
        for i, name in enumerate(events, 1):
            out(f"[{i}] {name}: {mode_for(name, policies)}")
        out("[a] 添加自定义事件  [s] 保存  [0] 取消")
        choice = ask("请选择事件：").strip().lower()
        if choice == "0":
            return original
        if choice == "s":
            names = ["default"] + [name for name in events if name in policies]
            result = ",".join(f"{name}={policies[name]}" for name in names)
            parse_modes(result)
            return result
        if choice == "a":
            name = ask("事件名称：").strip()
            if not _VALID_KIND.fullmatch(name) or name == "default":
                out("事件名称只能包含字母、数字和下划线")
                continue
            if name not in events:
                events.append(name)
        elif choice == "d":
            name = "default"
        elif choice.isdecimal() and 1 <= int(choice) <= len(events):
            name = events[int(choice) - 1]
        else:
            out("无效选项。")
            continue
        mode = ask(f"{name} 处理方式 [off/direct/agent/both]（回车保留）：").strip().lower()
        if not mode:
            continue
        if mode not in EVENT_MODES:
            out("仅支持 off、direct、agent、both")
            continue
        policies[name] = mode
