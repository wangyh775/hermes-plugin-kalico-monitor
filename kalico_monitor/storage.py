"""SQLite outbox: commit state changes and alerts in one transaction."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .events import Alert


class Outbox:
    def __init__(self, path: str | Path, printer: str):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.printer = printer
        self.conn = sqlite3.connect(str(self.path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, severity TEXT NOT NULL,
            filename TEXT NOT NULL, detail TEXT NOT NULL,
            created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            next_retry REAL NOT NULL DEFAULT 0, delivered REAL,
            last_error TEXT NOT NULL DEFAULT ''
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS alert_routes (
            alert_id TEXT NOT NULL, route TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, next_retry REAL NOT NULL DEFAULT 0,
            delivered REAL, last_error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(alert_id, route),
            FOREIGN KEY(alert_id) REFERENCES alerts(id)
        )""")
        if "payload" not in {row[1] for row in self.conn.execute("PRAGMA table_info(alerts)")}:
            self.conn.execute("ALTER TABLE alerts ADD COLUMN payload TEXT")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def get_state(self) -> dict | None:
        row = self.conn.execute("SELECT value FROM metadata WHERE key='watch_state'").fetchone()
        return json.loads(row["value"]) if row else None

    def get_meta(self, key: str, fallback=None):
        row = self.conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else fallback

    def _set_meta(self, key: str, value) -> None:
        self.conn.execute("INSERT OR REPLACE INTO metadata (key,value) VALUES (?,?)",
                          (key, json.dumps(value, ensure_ascii=False)))

    def set_meta(self, key: str, value) -> None:
        with self.conn:
            self._set_meta(key, value)

    def transition(self, state: dict, alerts: list[Alert]) -> None:
        """Atomic: pending alerts cannot disappear because state advanced first."""
        with self.conn:
            for alert in alerts:
                self._insert(alert)
            self._set_meta("watch_state", state)

    def report_checkpoint(self, key: str, tracker: dict, alert: Alert | None = None) -> None:
        """Queue a periodic report and its next deadline in one transaction."""
        with self.conn:
            if alert is not None:
                self._insert(alert)
            self._set_meta(key, tracker)

    def _insert(self, alert: Alert) -> str:
        digest = hashlib.sha256(f"{self.printer}|{alert.id_key}".encode()).hexdigest()[:32]
        self.conn.execute("""INSERT OR IGNORE INTO alerts
            (id,kind,severity,filename,detail,created,next_retry,payload)
            VALUES (?,?,?,?,?,?,?,?)""", (digest, alert.kind, alert.severity,
                            alert.filename, alert.detail, time.time(), time.time(),
                            json.dumps(alert.payload, ensure_ascii=False, allow_nan=False)
                            if alert.payload is not None else None))
        return digest

    def enqueue_source(self, alert: Alert) -> bool:
        """Insert once across reconnects and process restarts."""
        with self.conn:
            before = self.conn.total_changes
            self._insert(alert)
            return self.conn.total_changes > before

    def enqueue(self, alert: Alert) -> str:
        with self.conn:
            return self._insert(alert)

    def unrouted(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute("""SELECT a.* FROM alerts a WHERE NOT EXISTS
            (SELECT 1 FROM alert_routes r WHERE r.alert_id=a.id)
            AND a.delivered IS NULL ORDER BY a.created LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def ensure_routes(self, alert_id: str, routes: tuple[str, ...]) -> None:
        with self.conn:
            if not routes:
                self.conn.execute("UPDATE alerts SET delivered=?, last_error=? WHERE id=? AND delivered IS NULL AND NOT EXISTS (SELECT 1 FROM alert_routes WHERE alert_id=?)",
                                  (time.time(), "suppressed by event policy", alert_id, alert_id))
                return
            for route in routes:
                self.conn.execute("""INSERT OR IGNORE INTO alert_routes
                    (alert_id,route,next_retry) VALUES (?,?,?)""",
                                  (alert_id, route, time.time()))

    def pending_routes(self, now: float | None = None, limit: int = 20) -> list[dict]:
        now = time.time() if now is None else now
        rows = self.conn.execute("""SELECT a.*,r.route FROM alert_routes r
            JOIN alerts a ON a.id=r.alert_id WHERE r.delivered IS NULL
            AND r.next_retry<=? ORDER BY a.created LIMIT ?""", (now, limit)).fetchall()
        return [dict(r) for r in rows]

    def route_delivered(self, alert_id: str, route: str) -> None:
        with self.conn:
            self.conn.execute("""UPDATE alert_routes SET delivered=?,last_error=''
                WHERE alert_id=? AND route=?""", (time.time(), alert_id, route))
            outstanding = self.conn.execute("""SELECT COUNT(*) FROM alert_routes
                WHERE alert_id=? AND delivered IS NULL""", (alert_id,)).fetchone()[0]
            if outstanding == 0:
                self.conn.execute("""UPDATE alerts SET delivered=?,last_error=''
                    WHERE id=?""", (time.time(), alert_id))

    def route_failed(self, alert_id: str, route: str, error: str) -> None:
        row = self.conn.execute("""SELECT attempts FROM alert_routes
            WHERE alert_id=? AND route=?""", (alert_id, route)).fetchone()
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        delay = min(300, 2 ** min(attempts, 8))
        with self.conn:
            self.conn.execute("""UPDATE alert_routes SET attempts=?,next_retry=?,last_error=?
                WHERE alert_id=? AND route=?""",
                              (attempts, time.time() + delay, error[:240], alert_id, route))

    def pending(self, now: float | None = None, limit: int = 20) -> list[dict]:
        now = time.time() if now is None else now
        rows = self.conn.execute("""SELECT * FROM alerts WHERE delivered IS NULL
            AND next_retry<=? ORDER BY created LIMIT ?""", (now, limit)).fetchall()
        return [dict(row) for row in rows]

    def delivered(self, alert_id: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE alerts SET delivered=?,last_error='' WHERE id=?",
                              (time.time(), alert_id))

    def failed(self, alert_id: str, error: str) -> None:
        row = self.conn.execute("SELECT attempts FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        delay = min(300, 2 ** min(attempts, 8))
        with self.conn:
            self.conn.execute("""UPDATE alerts SET attempts=?,next_retry=?,last_error=?
                WHERE id=?""", (attempts, time.time() + delay, error[:240], alert_id))

    def summary(self) -> dict:
        counts = self.conn.execute("""SELECT
          SUM(CASE WHEN delivered IS NULL THEN 1 ELSE 0 END) pending,
          SUM(CASE WHEN delivered IS NOT NULL THEN 1 ELSE 0 END) delivered
          FROM alerts""").fetchone()
        rows = self.conn.execute("SELECT kind,severity,filename,detail,created,delivered FROM alerts ORDER BY created DESC LIMIT 10").fetchall()
        return {"printer": self.printer, "state": self.get_state(),
                "pending": counts["pending"] or 0,
                "delivered": counts["delivered"] or 0,
                "recent": [dict(r) for r in rows]}
