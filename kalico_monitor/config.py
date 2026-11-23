"""Settings from environment; secrets are never stored in code or repository."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


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

    @classmethod
    def load(cls) -> "Settings":
        mr = os.getenv("MOONRAKER_URL", "http://127.0.0.1:7125").rstrip("/")
        hermes = os.getenv("HERMES_WEBHOOK_URL", "http://127.0.0.1:8644/webhooks/kalico-alert")
        secret = os.getenv("HERMES_WEBHOOK_SECRET", "")
        if not secret or secret == "INSECURE_NO_AUTH":
            raise ValueError("HERMES_WEBHOOK_SECRET must be a real random secret")
        if urlparse(mr).scheme not in {"http", "https"} or not urlparse(mr).hostname:
            raise ValueError("MOONRAKER_URL must be an http(s) origin")
        parsed = urlparse(hermes)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("HERMES_WEBHOOK_URL is invalid")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Hermes Webhook must use HTTPS except for localhost")
        agent_url = os.getenv("HERMES_AGENT_WEBHOOK_URL", "").strip()
        if agent_url:
            agent = urlparse(agent_url)
            if agent.scheme not in {"http", "https"} or not agent.hostname:
                raise ValueError("HERMES_AGENT_WEBHOOK_URL is invalid")
            if agent.scheme == "http" and agent.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Agent Webhook must use HTTPS except for localhost")
        modes = os.getenv("KALICO_EVENT_MODES", "").strip()
        from .policy import parse_modes
        parse_modes(modes)
        base = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        db = Path(os.getenv("KALICO_MONITOR_DB", str(base / "kalico-monitor/alerts.sqlite3"))).expanduser()
        report_minutes = int(os.getenv("KALICO_PROGRESS_REPORT_MINUTES", "0"))
        if report_minutes < 0 or report_minutes > 1440:
            raise ValueError("KALICO_PROGRESS_REPORT_MINUTES must be between 0 and 1440")
        if 0 < report_minutes < 5:
            raise ValueError("Progress reporting interval must be at least 5 minutes")
        raw_force = os.getenv("KALICO_WATCH_NOZZLE_FORCE", "0").strip().lower()
        if raw_force not in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
            raise ValueError("KALICO_WATCH_NOZZLE_FORCE must be a boolean")
        watch_force = raw_force in {"1", "true", "yes", "on"}
        return cls(printer=os.getenv("KALICO_PRINTER_NAME", "PEEK-01"),
                   moonraker_url=mr, moonraker_api_key=os.getenv("MOONRAKER_API_KEY", ""),
                   hermes_url=hermes, hermes_secret=secret, sqlite_path=db,
                   disconnect_seconds=max(5, int(os.getenv("KALICO_DISCONNECT_SECONDS", "25"))),
                   stall_minutes=max(0, int(os.getenv("KALICO_STALL_MINUTES", "0"))),
                   progress_report_minutes=report_minutes, watch_nozzle_force=watch_force,
                   hermes_agent_url=agent_url, event_modes=modes)

    @property
    def ws_url(self) -> str:
        return self.moonraker_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/websocket"
