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
        return cls(printer=os.getenv("KALICO_PRINTER_NAME", "PEEK-01"),
                   moonraker_url=mr, moonraker_api_key=os.getenv("MOONRAKER_API_KEY", ""),
                   hermes_url=hermes, hermes_secret=secret, sqlite_path=db,
                   disconnect_seconds=max(5, int(os.getenv("KALICO_DISCONNECT_SECONDS", "25"))),
                   stall_minutes=max(0, int(os.getenv("KALICO_STALL_MINUTES", "0"))))

    @property
    def ws_url(self) -> str:
        return self.moonraker_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/websocket"
