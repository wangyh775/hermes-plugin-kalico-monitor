"""Settings from environment; secrets are never stored in code or repository."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .custom_config import EventRule, Reliability, load_extensions, load_handlers, load_reliability
from .handlers import HandlerConfig
from .handlers import agent_url as resolve_agent_url


@dataclass(frozen=True)
class Settings:
    printer: str
    moonraker_url: str
    moonraker_api_key: str
    hermes_url: str
    hermes_secret: str
    sqlite_path: Path
    disconnect_seconds: int
    stall_minutes: int
    progress_report_minutes: int = 0
    watch_nozzle_force: bool = False
    hermes_agent_url: str = ""
    event_modes: str = ""
    message_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    event_rules: tuple[EventRule, ...] = ()
    handler_config: HandlerConfig = field(default_factory=HandlerConfig)
    reliability: Reliability = field(default_factory=Reliability)
    chamber_object: str = ""

    @classmethod
    def load(cls) -> "Settings":
        from .monitor_cfg import cfg_path, read_cfg
        env_path = Path(os.getenv("KALICO_MONITOR_ENV_FILE", "~/.config/kalico-monitor/monitor.env")).expanduser()
        path = cfg_path(env_path)
        overrides = read_cfg(path)
        message_fields, event_rules = load_extensions(path)
        handler_config = load_handlers(path)

        def get(key: str, default: str = "") -> str:
            return overrides.get(key, os.getenv(key, default))

        mr = get("MOONRAKER_URL", "http://127.0.0.1:7125").rstrip("/")
        hermes = get("HERMES_WEBHOOK_URL", "http://127.0.0.1:8644/webhooks/kalico-alert")
        secret = get("HERMES_WEBHOOK_SECRET", "")
        if not secret or secret == "INSECURE_NO_AUTH":
            raise ValueError("HERMES_WEBHOOK_SECRET must be a real random secret")
        if urlparse(mr).scheme not in {"http", "https"} or not urlparse(mr).hostname:
            raise ValueError("MOONRAKER_URL must be an http(s) origin")
        parsed = urlparse(hermes)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("HERMES_WEBHOOK_URL is invalid")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Hermes Webhook must use HTTPS except for localhost")
        agent_url = get("HERMES_AGENT_WEBHOOK_URL", "").strip()
        if agent_url:
            agent = urlparse(agent_url)
            if agent.scheme not in {"http", "https"} or not agent.hostname:
                raise ValueError("HERMES_AGENT_WEBHOOK_URL is invalid")
            if agent.scheme == "http" and agent.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Agent Webhook must use HTTPS except for localhost")
        for handler in handler_config.handlers.values():
            resolve_agent_url(agent_url, handler)
        modes = get("KALICO_EVENT_MODES", "").strip()
        from .policy import parse_modes, validate_modes
        validate_modes(parse_modes(modes), event_rules)
        base = Path(get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        db = Path(get("KALICO_MONITOR_DB", str(base / "kalico-monitor/alerts.sqlite3"))).expanduser()
        report_minutes = int(get("KALICO_PROGRESS_REPORT_MINUTES", "0"))
        if report_minutes < 0 or report_minutes > 1440:
            raise ValueError("KALICO_PROGRESS_REPORT_MINUTES must be between 0 and 1440")
        if 0 < report_minutes < 5:
            raise ValueError("Progress reporting interval must be at least 5 minutes")
        raw_force = get("KALICO_WATCH_NOZZLE_FORCE", "0").strip().lower()
        if raw_force not in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
            raise ValueError("KALICO_WATCH_NOZZLE_FORCE must be a boolean")
        watch_force = raw_force in {"1", "true", "yes", "on"}
        return cls(printer=get("KALICO_PRINTER_NAME", "PEEK-01"),
                   moonraker_url=mr, moonraker_api_key=get("MOONRAKER_API_KEY", ""),
                   hermes_url=hermes, hermes_secret=secret, sqlite_path=db,
                   disconnect_seconds=max(5, int(get("KALICO_DISCONNECT_SECONDS", "25"))),
                   stall_minutes=max(0, int(get("KALICO_STALL_MINUTES", "0"))),
                   progress_report_minutes=report_minutes, watch_nozzle_force=watch_force,
                   hermes_agent_url=agent_url, event_modes=modes,
                    message_fields=message_fields, event_rules=event_rules,
                    handler_config=handler_config, reliability=load_reliability(path),
                    chamber_object=get("KALICO_CHAMBER_OBJECT"))

    @property
    def ws_url(self) -> str:
        return self.moonraker_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/websocket"
