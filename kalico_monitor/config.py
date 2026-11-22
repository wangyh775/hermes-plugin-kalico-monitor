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
        base = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        db = Path(os.getenv("KALICO_MONITOR_DB", str(base / "kalico-monitor/alerts.sqlite3"))).expanduser()
        report_minutes = int(os.getenv("KALICO_PROGRESS_REPORT_MINUTES", "0"))
        if report_minutes < 0 or report_minutes > 1440:
            raise ValueError("KALICO_PROGRESS_REPORT_MINUTES must be between 0 and 1440")
        if 0 < report_minutes < 5:
            raise ValueError("Progress reporting interval must be at least 5 minutes")
        return cls(printer=os.getenv("KALICO_PRINTER_NAME", "PEEK-01"),
                   moonraker_url=mr, moonraker_api_key=os.getenv("MOONRAKER_API_KEY", ""),
                   hermes_url=hermes, hermes_secret=secret, sqlite_path=db,
                   disconnect_seconds=max(5, int(os.getenv("KALICO_DISCONNECT_SECONDS", "25"))),
                   stall_minutes=max(0, int(os.getenv("KALICO_STALL_MINUTES", "0"))),
                   progress_report_minutes=report_minutes)

    @property
    def ws_url(self) -> str:
        return self.moonraker_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/websocket"
