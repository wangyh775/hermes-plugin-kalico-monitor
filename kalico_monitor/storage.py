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
        self.conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
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
        # Existing SQLite databases must survive an in-place upgrade.
        for table in ("alerts", "alert_routes"):
            columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if "suppressed" not in columns:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN suppressed REAL")
        # v0.2 marked discarded events as delivered. Correct the old counters.
        self.conn.execute("""UPDATE alerts SET suppressed=delivered, delivered=NULL
            WHERE last_error='suppressed by event policy' AND delivered IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM alert_routes r
                            WHERE r.alert_id=alerts.id AND r.delivered IS NOT NULL)""")
        # The filament_force bridge persists structured incident payloads.
        if "payload" not in {row[1] for row in self.conn.execute("PRAGMA table_info(alerts)")}:
            self.conn.execute("ALTER TABLE alerts ADD COLUMN payload TEXT")
        for table in ("alerts", "alert_routes"):
            if "failed_at" not in {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN failed_at REAL")
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(alerts)")}
        if "incident_key" not in columns:
            self.conn.execute("ALTER TABLE alerts ADD COLUMN incident_key TEXT")
            self.conn.execute("ALTER TABLE alerts ADD COLUMN source_seq INTEGER")
            self.conn.execute("""UPDATE alerts SET
                incident_key=COALESCE(json_extract(payload,'$.incident_id'), json_extract(payload,'$.event_id')),
                source_seq=json_extract(payload,'$.seq')
                WHERE kind LIKE 'filament_%' AND json_valid(payload)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS alerts_incident_seq ON alerts(incident_key,source_seq)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS routes_ready ON alert_routes(route,delivered,suppressed,failed_at,next_retry)")
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
            (id,kind,severity,filename,detail,created,next_retry,payload,incident_key,source_seq)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (digest, alert.kind, alert.severity,
                            alert.filename, alert.detail, time.time(), time.time(),
                            json.dumps(alert.payload, ensure_ascii=False, allow_nan=False)
                            if alert.payload is not None else None,
                            (alert.payload.get("incident_id") or alert.payload.get("event_id"))
                            if isinstance(alert.payload, dict) else None,
                            alert.payload.get("seq") if isinstance(alert.payload, dict)
                            and type(alert.payload.get("seq")) is int else None))
        return digest

    def enqueue_source(self, alert: Alert) -> bool:
        """Insert once across reconnects and process restarts."""
        with self.conn:
            before = self.conn.total_changes
            self._insert(alert)
            return self.conn.total_changes > before

    def ingest_filament_event(self, alert: Alert, boot: str, seq: int) -> bool:
        """Atomically record event, possible gap, and per-boot high-water mark.

        First sight of a boot establishes the observation boundary. Older
        replay can fill a previously reported gap, but never rolls back the cursor.
        """
        key = f"filament_cursor:{self.printer}:{boot}"
        with self.conn:
            previous = self.get_meta(key)
            if type(previous) is int and seq > previous + 1:
                first, last = previous + 1, seq - 1
                self._insert(Alert(
                    kind="filament_event_gap", generation=alert.generation,
                    filename=alert.filename, severity="warning",
                    detail=f"受力事件流不连续：可能缺失 {first}–{last}（启动 {boot}）；请检查打印机状态",
                    source_id=f"{boot}:{first}-{last}",
                    payload={"boot_id": boot, "missing_from_seq": first,
                             "missing_to_seq": last, "observed_seq": seq,
                             "missing_count": last - first + 1},
                ))
            before = self.conn.total_changes
            self._insert(alert)
            inserted = self.conn.total_changes > before
            if type(previous) is not int or seq > previous:
                self._set_meta(key, seq)
            return inserted

    def enqueue(self, alert: Alert) -> str:
        with self.conn:
            return self._insert(alert)

    def unrouted(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute("""SELECT a.* FROM alerts a WHERE NOT EXISTS
            (SELECT 1 FROM alert_routes r WHERE r.alert_id=a.id)
            AND a.delivered IS NULL AND a.suppressed IS NULL AND a.failed_at IS NULL
            ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, a.created LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def _finalize_routes(self, alert_id: str) -> None:
        """Finish once every route was delivered or suppressed, never before."""
        counts = self.conn.execute("""SELECT
            SUM(CASE WHEN delivered IS NULL AND suppressed IS NULL AND failed_at IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN delivered IS NOT NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN failed_at IS NOT NULL THEN 1 ELSE 0 END)
            FROM alert_routes WHERE alert_id=?""", (alert_id,)).fetchone()
        if counts[0]:
            return
        if counts[2]:
            self.conn.execute("""UPDATE alerts SET failed_at=?, last_error='one or more routes permanently failed'
                WHERE id=? AND delivered IS NULL AND suppressed IS NULL AND failed_at IS NULL""",
                (time.time(), alert_id))
        elif counts[1]:
            self.conn.execute("""UPDATE alerts
                SET delivered=?, suppressed=NULL, last_error=''
                WHERE id=? AND delivered IS NULL AND suppressed IS NULL""",
                (time.time(), alert_id))
        else:
            self.conn.execute("""UPDATE alerts
                SET suppressed=?, last_error='suppressed by event policy'
                WHERE id=? AND delivered IS NULL AND suppressed IS NULL""",
                (time.time(), alert_id))

    def ensure_routes(self, alert_id: str, routes: tuple[str, ...]) -> None:
        with self.conn:
            if not routes:
                # Store a distinct suppressed terminal state, not a fake delivery.
                self.conn.execute("""UPDATE alerts
                    SET suppressed=?, last_error='suppressed by event policy'
                    WHERE id=? AND delivered IS NULL AND suppressed IS NULL
                    AND NOT EXISTS (SELECT 1 FROM alert_routes WHERE alert_id=?)""",
                    (time.time(), alert_id, alert_id))
                return
            for route in routes:
                self.conn.execute("""INSERT OR IGNORE INTO alert_routes
                    (alert_id,route,next_retry) VALUES (?,?,?)""",
                    (alert_id, route, time.time()))

    def reconcile_policy(self, modes: dict[str, str]) -> int:
        """Stop old queued retries whose routes are no longer allowed.

        A previous delivered route stays recorded, but undelivered Agent/Direct
        jobs are suppressed if the updated policy removes them. This is run
        at daemon startup after parsing the currently effective configuration.
        """
        from .policy import routes_for

        suppressed = 0
        with self.conn:
            rows = self.conn.execute("""SELECT r.alert_id, a.kind, r.route
                FROM alert_routes r JOIN alerts a ON a.id=r.alert_id
                WHERE r.delivered IS NULL AND r.suppressed IS NULL AND r.failed_at IS NULL""").fetchall()
            affected: set[str] = set()
            now = time.time()
            for row in rows:
                if row["route"] not in routes_for(row["kind"], modes):
                    self.conn.execute("""UPDATE alert_routes
                        SET suppressed=?, last_error='suppressed by event policy'
                        WHERE alert_id=? AND route=? AND delivered IS NULL
                          AND suppressed IS NULL""",
                        (now, row["alert_id"], row["route"]))
                    affected.add(row["alert_id"])
                    suppressed += 1
            for alert_id in affected:
                self._finalize_routes(alert_id)
        return suppressed

    def pending_routes(self, now: float | None = None, limit: int = 20,
                       *, route: str | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rows = self.conn.execute("""SELECT a.*,r.route FROM alert_routes r
            JOIN alerts a ON a.id=r.alert_id WHERE r.delivered IS NULL
            AND r.suppressed IS NULL AND a.suppressed IS NULL AND r.failed_at IS NULL
            AND a.failed_at IS NULL
            AND r.next_retry<=? AND (? IS NULL OR r.route=?)
            AND (a.incident_key IS NULL OR NOT EXISTS (
                SELECT 1 FROM alerts p WHERE p.incident_key=a.incident_key
                AND p.source_seq<a.source_seq AND p.suppressed IS NULL
                AND p.failed_at IS NULL AND p.delivered IS NULL
                AND (NOT EXISTS (SELECT 1 FROM alert_routes anyr WHERE anyr.alert_id=p.id)
                    OR EXISTS (SELECT 1 FROM alert_routes pr WHERE pr.alert_id=p.id
                        AND pr.route=r.route AND pr.delivered IS NULL
                        AND pr.suppressed IS NULL AND pr.failed_at IS NULL))
            ) OR (a.severity='critical' AND a.created<=?-60))
            ORDER BY CASE a.severity WHEN 'critical' THEN 0
                WHEN 'warning' THEN 1 ELSE 2 END, a.created LIMIT ?""",
            (now, route, route, now, limit)).fetchall()
        return [dict(r) for r in rows]

    def route_delivered(self, alert_id: str, route: str) -> None:
        with self.conn:
            self.conn.execute("""UPDATE alert_routes SET delivered=?,last_error=''
                WHERE alert_id=? AND route=?""", (time.time(), alert_id, route))
            self._finalize_routes(alert_id)

    def route_dead_letter(self, alert_id: str, route: str, error: str) -> None:
        with self.conn:
            self.conn.execute("""UPDATE alert_routes SET failed_at=?,attempts=attempts+1,last_error=?
                WHERE alert_id=? AND route=? AND delivered IS NULL AND suppressed IS NULL
                AND failed_at IS NULL""", (time.time(), error[:240], alert_id, route))
            self._finalize_routes(alert_id)

    def dead_letters(self, limit: int = 100) -> list[dict]:
        return [dict(row) for row in self.conn.execute("""SELECT a.kind,a.filename,r.*
            FROM alert_routes r JOIN alerts a ON a.id=r.alert_id
            WHERE r.failed_at IS NOT NULL ORDER BY r.failed_at DESC LIMIT ?""", (limit,))]

    def retry_dead_letter(self, alert_id: str, route: str) -> bool:
        with self.conn:
            result = self.conn.execute("""UPDATE alert_routes SET failed_at=NULL,attempts=0,
                next_retry=0,last_error='' WHERE alert_id=? AND route=? AND failed_at IS NOT NULL""",
                (alert_id, route))
            if result.rowcount:
                self.conn.execute("UPDATE alerts SET failed_at=NULL,last_error='' WHERE id=?", (alert_id,))
            return bool(result.rowcount)

    def route_failed(self, alert_id: str, route: str, error: str) -> None:
        row = self.conn.execute("""SELECT attempts FROM alert_routes
            WHERE alert_id=? AND route=?""", (alert_id, route)).fetchone()
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        delay = min(300, 2 ** min(attempts, 8))
        with self.conn:
            self.conn.execute("""UPDATE alert_routes SET attempts=?,next_retry=?,last_error=?
                WHERE alert_id=? AND route=? AND suppressed IS NULL AND delivered IS NULL""",
                              (attempts, time.time() + delay, error[:240], alert_id, route))

    def pending(self, now: float | None = None, limit: int = 20) -> list[dict]:
        now = time.time() if now is None else now
        rows = self.conn.execute("""SELECT * FROM alerts WHERE delivered IS NULL
            AND suppressed IS NULL AND failed_at IS NULL AND next_retry<=?
            ORDER BY created LIMIT ?""", (now, limit)).fetchall()
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
          SUM(CASE WHEN delivered IS NULL AND suppressed IS NULL AND failed_at IS NULL THEN 1 ELSE 0 END) pending,
          SUM(CASE WHEN failed_at IS NOT NULL THEN 1 ELSE 0 END) failed,
          SUM(CASE WHEN delivered IS NOT NULL THEN 1 ELSE 0 END) delivered,
          SUM(CASE WHEN suppressed IS NOT NULL THEN 1 ELSE 0 END) suppressed
          FROM alerts""").fetchone()
        rows = self.conn.execute("""SELECT kind,severity,filename,detail,
            created,delivered,suppressed,failed_at FROM alerts ORDER BY created DESC LIMIT 10""").fetchall()
        return {"printer": self.printer, "state": self.get_state(),
                "pending": counts["pending"] or 0,
                "delivered": counts["delivered"] or 0,
                "suppressed": counts["suppressed"] or 0,
                "failed": counts["failed"] or 0,
                "dead_routes": self.conn.execute("SELECT COUNT(*) FROM alert_routes WHERE failed_at IS NOT NULL").fetchone()[0],
                "recent": [dict(r) for r in rows]}
