"""Validated, read-only extensions for Monitor events and Direct notifications.

These sections configure event generation/routing only. They never execute
G-code, call Kiln, evaluate Python expressions, or control a printer.
"""
from __future__ import annotations

import configparser
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .policy import EVENT_MODES

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
OBJECT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?: [A-Za-z0-9_-]+)?$")
FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
MESSAGE_FIELDS = {
    "printer", "kind", "severity", "occurred_at", "filename", "detail", "alert_id",
    "progress", "elapsed", "remaining", "nozzle", "bed", "chamber",
    "layer", "total_layers", "force_g", "e_speed_mm_s",
    "diagnostic_speed_mm_s", "result", "reason", "step_mm", "total_mm",
    "window_summary", "object", "field", "value", "threshold",
    "source_type", "source", "source_method", "source_object", "trigger",
    "received_at", "queued_at", "source_occurred_at", "previous_state", "current_state",
    "klippy_state", "params_summary", "service", "service_state", "throttle_bits",
    "incident_id", "source_event_id", "generation", "sequence",
    "incident_key", "source_eventtime", "source_clock_id", "generated_at", "payload_degraded",
    "gcode_response", "action", "queue_state", "name", "job_filename",
}
DEFAULT_MESSAGE_FIELDS = (
    "printer", "kind", "severity", "occurred_at", "filename", "detail", "alert_id",
)
COMPARATORS = {"gt", "ge", "lt", "le"}
SEVERITIES = {"info", "warning", "critical"}
BASE_SECTIONS = {"monitor", "events", "hermes", "printer", "status"}


@dataclass(frozen=True)
class Reliability:
    subscription_refresh: int = 30
    subscription_timeout: int = 20
    observation_gap: int = 35
    field_max_age: int = 120
    connect_timeout: int = 5

    def __post_init__(self):
        if not (5 <= self.subscription_refresh <= 120 and
                5 <= self.subscription_timeout <= 60 and
                self.subscription_refresh < self.observation_gap <= self.field_max_age <= 600 and
                1 <= self.connect_timeout <= 30):
            raise ValueError("Invalid [reliability] time bounds")


def load_reliability(path: Path) -> Reliability:
    parser = _parser(path)
    if not parser.has_section("reliability"):
        return Reliability()
    values = dict(parser.items("reliability"))
    if set(values) - set(Reliability.__dataclass_fields__):
        raise ValueError("Unknown [reliability] option")
    return Reliability(**{key: int(value) for key, value in values.items()})


@dataclass(frozen=True)
class EventRule:
    name: str
    object: str
    field: str
    condition: str
    threshold: float
    duration: float
    cooldown: float
    severity: str
    mode: str


def parse_field_list(value: str) -> tuple[str, ...]:
    fields = tuple(x.strip() for x in value.split(","))
    if not fields or any(not NAME.fullmatch(x) or x not in MESSAGE_FIELDS for x in fields):
        raise ValueError("Direct 消息字段无效；参见 MESSAGE_FIELDS 字段白名单")
    if len(set(fields)) != len(fields):
        raise ValueError("Direct 消息字段不得重复")
    return fields


def _parser(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True, delimiters=(":", "="))
    parser.optionxform = str
    if path.exists():
        if path.is_symlink():
            raise ValueError(f"拒绝读取符号链接形式的 Monitor CFG：{path}")
        try:
            with path.open(encoding="utf-8") as f:
                parser.read_file(f)
        except (configparser.Error, UnicodeError) as exc:
            raise ValueError(f"Monitor CFG 格式错误：{exc}") from exc
    return parser


def load_extensions(path: Path) -> tuple[dict[str, tuple[str, ...]], tuple[EventRule, ...]]:
    """Validate optional [message ...] and [event ...] sections strictly."""
    parser = _parser(path)
    messages = {"default": DEFAULT_MESSAGE_FIELDS}
    rules: list[EventRule] = []
    for section in parser.sections():
        if section in BASE_SECTIONS:
            continue
        if section == "reliability":
            load_reliability(path)
            continue
        if section == "message" or section.startswith("message "):
            name = "default" if section == "message" else section[8:]
            if not NAME.fullmatch(name):
                raise ValueError(f"消息模板名称无效：{section}")
            options = dict(parser.items(section))
            if set(options) not in ({"fields"}, {"template"}):
                raise ValueError(f"{section} 必须且只能设置 fields 或 template")
            if "fields" in options:
                messages[name] = parse_field_list(options["fields"])
            else:
                # An event-specific template overrides default fields.
                messages[name] = DEFAULT_MESSAGE_FIELDS
        elif section == "handlers" or section.startswith("handler "):
            continue
        elif section.startswith("event "):
            name = section[6:]
            if not NAME.fullmatch(name) or name == "default":
                raise ValueError(f"自定义事件名称无效：{section}")
            from .event_registry import REGISTRY
            if name in REGISTRY:
                raise ValueError(f"自定义事件不得覆盖内置事件：{name}")
            options = dict(parser.items(section))
            required = {"source", "object", "field", "condition", "threshold"}
            allowed = required | {"duration", "cooldown", "severity", "mode"}
            if not required <= options.keys() or set(options) - allowed:
                raise ValueError(f"{section} 必须指定 {sorted(required)}，且不得包含未知键")
            if options["source"] != "moonraker":
                raise ValueError(f"{section} 当前仅支持 source: moonraker")
            obj, field = options["object"], options["field"]
            if not OBJECT.fullmatch(obj) or not FIELD.fullmatch(field):
                raise ValueError(f"{section} 的对象或字段名称非法")
            condition = options["condition"]
            if condition not in COMPARATORS:
                raise ValueError(f"{section} condition 只支持 gt/ge/lt/le")
            try:
                threshold = float(options["threshold"])
                duration = float(options.get("duration", "0"))
                cooldown = float(options.get("cooldown", "0"))
            except ValueError as exc:
                raise ValueError(f"{section} 阈值和时间必须为数字") from exc
            if not all(math.isfinite(x) for x in (threshold, duration, cooldown)):
                raise ValueError(f"{section} 不允许 NaN/Infinity")
            if not (0 <= duration <= 86400 and 0 <= cooldown <= 86400):
                raise ValueError(f"{section} duration/cooldown 必须在 0..86400 秒")
            severity = options.get("severity", "warning")
            mode = options.get("mode", "direct")
            if severity not in SEVERITIES or mode not in EVENT_MODES:
                raise ValueError(f"{section} 的 severity 或 mode 无效")
            rules.append(EventRule(name, obj, field, condition, threshold,
                                   duration, cooldown, severity, mode))
            if len(rules) > 32:
                raise ValueError("自定义事件最多 32 个")
        else:
            raise ValueError(f"不支持的 monitor.cfg 配置节：{section}")
    from .event_registry import definition
    from .handlers import parse_handlers
    from .policy import validate_modes
    if parser.has_section("events"):
        validate_modes(dict(parser.items("events")), rules)
    for kind in messages:
        if kind != "default":
            entry = definition(kind, rules)
            if not entry or not entry.forwardable:
                raise ValueError(f"消息配置引用未知或不可转发事件：{kind}")
    parse_handlers(parser, rules, MESSAGE_FIELDS)
    return messages, tuple(rules)


def load_handlers(path: Path):
    from .handlers import parse_handlers
    _, rules = load_extensions(path)
    return parse_handlers(_parser(path), rules, MESSAGE_FIELDS)


def preserved_extension_text(path: Path) -> str:
    """Preserve validated extensions when the interactive wizard rewrites CFG."""
    load_extensions(path)
    parser = _parser(path)
    lines: list[str] = []
    for section in parser.sections():
        if section in BASE_SECTIONS:
            continue
        lines.extend(["", f"[{section}]"])
        for key, value in parser.items(section):
            # ConfigParser returns multiline values without indentation.
            lines.append(f"{key}: " + value.replace("\n", "\n    "))
    return "\n".join(lines) + "\n" if lines else ""
