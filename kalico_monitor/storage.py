"""SQLite outbox: commit state changes and alerts in one transaction."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .event_registry import metadata, utc_timestamp
from .events import Alert


def safe_text(value, limit=240):
    return str(value).encode("utf-8", "replace").decode("utf-8")[:limit]


@dataclass(frozen=True)
class WriteResult:
    id: str
    status: str


class Outbox:
    def __init__(self, path: str | Path, printer: str):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.printer = printer
        self.conn = sqlite3.connect(str(self.path), timeout=0.25)
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
        if "origin" not in {row[1] for row in self.conn.execute("PRAGMA table_info(alerts)")}:
            self.conn.execute("ALTER TABLE alerts ADD COLUMN origin TEXT")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS dead_letters (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, route TEXT NOT NULL,
            reason TEXT NOT NULL, created REAL NOT NULL, data TEXT NOT NULL)""")
        for name, sql in (
            ("alerts_unfinished", "ON alerts(severity,created) WHERE delivered IS NULL AND suppressed IS NULL"),
            ("routes_due", "ON alert_routes(route,next_retry,alert_id) WHERE delivered IS NULL AND suppressed IS NULL"),
            ("alerts_terminal", "ON alerts(created) WHERE delivered IS NOT NULL OR suppressed IS NOT NULL"),
        ):
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS {name} {sql}")
        if "incident_key" not in {row[1] for row in self.conn.execute("PRAGMA table_info(alerts)")}:
            self.conn.execute("ALTER TABLE alerts ADD COLUMN incident_key TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS alerts_incident ON alerts(incident_key,created)")
        self._migrate_incidents()
        self.conn.commit()

    def _migrate_incidents(self):
        if self.get_meta("incident_migration_v1", False):
            return
        kinds = {"moonraker_unreachable": "moonraker", "moonraker_recovered": "moonraker",
                 "klippy_disconnected": "klippy", "klippy_recovered": "klippy",
                 "klippy_shutdown": "klippy_shutdown", "klippy_shutdown_recovered": "klippy_shutdown"}
        for row in self.conn.execute("SELECT id,kind,payload,origin FROM alerts WHERE incident_key IS NULL"):
            prefix = kinds.get(row["kind"])
            if prefix is None:
                continue
            try:
                payload = json.loads(row["payload"] or "{}")
                origin = json.loads(row["origin"] or "{}")
                generation = payload.get("incident_id")
                if generation is None and prefix == "klippy_shutdown":
                    generation = origin.get("generation")
            except (ValueError, TypeError, AttributeError):
                continue
            if type(generation) is int and generation > 0:
                self.conn.execute("UPDATE alerts SET incident_key=? WHERE id=?",
                                  (f"{prefix}:{generation}", row["id"]))
        # Recover a previously committed Ready state with a still-open shutdown
        # incident left by the non-atomic v2 recovery path.
        state = self.get_state() or {}
        opened = self.get_meta("shutdown_open_incident", {})
        if state.get("klippy_state") == "ready" and opened.get("id"):
            generation = opened["id"]
            self._insert(Alert("klippy_shutdown_recovered", generation, "", "Klippy ready (recovered during migration)",
                               "info", payload={"incident_id": generation,
                               "incident_key": f"klippy_shutdown:{generation}"}, trigger="migration"))
            self._set_meta("shutdown_open_incident", {})
        self._set_meta("incident_migration_v1", True)

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
                          (key, json.dumps(value, ensure_ascii=True, allow_nan=False)))

    def set_meta(self, key: str, value) -> None:
        with self.conn:
            self._set_meta(key, value)

    def transition(self, state: dict, alerts: list[Alert], incident=None, closed=()) -> None:
        """Atomic: pending alerts cannot disappear because state advanced first."""
        with self.conn:
            for alert in alerts:
                self._insert(alert)
            self._set_meta("watch_state", state)
            if incident is not None:
                self._set_meta("shutdown_open_incident", incident)
            for key in closed:
                self._set_meta(key, {})

    def report_checkpoint(self, key: str, tracker: dict, alert: Alert | None = None) -> None:
        """Queue a periodic report and its next deadline in one transaction."""
        with self.conn:
            if alert is not None:
                self._insert(alert)
            self._set_meta(key, tracker)

    def alert_id(self, alert: Alert) -> str:
        return hashlib.sha256(f"{self.printer}|{alert.id_key}".encode("utf-8", "backslashreplace")).hexdigest()[:32]

    def _quarantine(self, key, kind, route, reason, data):
        self.conn.execute("""INSERT OR IGNORE INTO dead_letters
            (id,kind,route,reason,created,data) VALUES (?,?,?,?,?,?)""",
            (key, str(kind).encode("utf-8", "replace").decode()[:100], route,
             str(reason).encode("utf-8", "replace").decode()[:300], time.time(),
             json.dumps({"preview": safe_text(data, 3000)}, ensure_ascii=True)))

    def quarantine(self, key, kind, reason, data):
        with self.conn:
            self._quarantine(key, kind, "ingest", reason, data)

    def _insert(self, alert: Alert, *, fallback=True, stable_id=None) -> WriteResult:
        digest = stable_id or self.alert_id(alert)
        if self.conn.execute('SELECT 1 FROM alerts WHERE id=?', (digest,)).fetchone():
            return WriteResult(digest, 'duplicate')
        try:
            from .event_registry import validate_payload
            validate_payload(alert.kind, alert.payload, alert.severity)
            for value in (alert.kind, alert.filename, alert.detail, alert.severity):
                value.encode("utf-8")
            payload = json.dumps(alert.payload, ensure_ascii=False, allow_nan=False) if alert.payload is not None else None
            if payload is not None:
                payload.encode("utf-8")
            origin = json.dumps(metadata(alert.kind, alert.payload, alert.source_method,
                                         alert.trigger) | {"received_at": utc_timestamp(alert.received_at),
                                                           "source_event_id": alert.source_id,
                                                           "generation": alert.generation,
                                                           "sequence": alert.sequence,
                                                           "source_eventtime": alert.source_eventtime,
                                                           "source_clock_id": alert.source_clock_id,
                                                           "generated_at": utc_timestamp(alert.generated_at)},
                                ensure_ascii=False, allow_nan=False)
            origin.encode("utf-8")
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
            self._quarantine(digest, alert.kind, "ingest", type(exc).__name__,
                             {"detail": alert.detail})
            if fallback and alert.severity == "critical":
                # Retain the stable identity and safe incident linkage, discard
                # optional data that would prevent notification.
                original = alert.payload if isinstance(alert.payload, dict) else {}
                minimal = {"payload_degraded": True}
                for key in ("incident_id", "incident_key"):
                    value = original.get(key)
                    if type(value) is int or isinstance(value, str):
                        minimal[key] = safe_text(value) if isinstance(value, str) else value
                from .event_registry import NATIVE
                if alert.kind in NATIVE:
                    minimal["params"] = []
                result = self._insert(replace(alert, detail="Critical event detected; diagnostic payload quarantined",
                    filename=safe_text(alert.filename, 180), payload=minimal,
                    source_eventtime=None, source_clock_id=None,
                    source_method=None, trigger="payload_degraded", received_at=time.time(), generated_at=time.time(),
                    source_id=safe_text(alert.source_id) if alert.source_id is not None else None),
                    fallback=False, stable_id=digest)
                if result.status != "quarantined":
                    return WriteResult(result.id, "degraded")
                raise ValueError("critical event cannot be persisted") from exc
            return WriteResult(digest, "quarantined")
        incident = (alert.payload or {}).get("incident_key")
        self.conn.execute("""INSERT OR IGNORE INTO alerts
            (id,kind,severity,filename,detail,created,next_retry,payload,origin,incident_key)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (digest, alert.kind, alert.severity,
                            alert.filename, alert.detail, time.time(), time.time(),
                            payload, origin, incident if isinstance(incident, str) else None))
        return WriteResult(digest, 'inserted')

    def enrich_alert(self, alert_id: str, detail: str) -> None:
        detail = detail.encode("utf-8", "replace").decode("utf-8")[:1500]
        with self.conn:
            self._set_meta(f"shutdown_detail:{alert_id}", detail)
            self.conn.execute("""UPDATE alerts SET detail=? WHERE id=? AND delivered IS NULL
                AND NOT EXISTS (SELECT 1 FROM alert_routes WHERE alert_id=?)""",
                (detail, alert_id, alert_id))

    def route_dead(self, alert_id, route, reason):
        with self.conn:
            self._quarantine(f"{alert_id}:{route}", "delivery", route, reason, {"alert_id": alert_id})
            self.conn.execute("""UPDATE alert_routes SET suppressed=?,last_error=?
                WHERE alert_id=? AND route=? AND delivered IS NULL AND suppressed IS NULL""",
                (time.time(), "permanent failure: " + safe_text(reason, 180), alert_id, route))
            self._finalize_routes(alert_id)

    def has_alert(self, alert: Alert) -> bool:
        return self.conn.execute("SELECT 1 FROM alerts WHERE id=?", (self.alert_id(alert),)).fetchone() is not None

    def journal_checkpoint(self, cursor: dict, alert: Alert, gap: Alert | None = None) -> bool:
        with self.conn:
            digest = self.alert_id(alert)
            existed = self.conn.execute('SELECT 1 FROM alerts WHERE id=?', (digest,)).fetchone()
            if gap is not None:
                self._insert(gap)
            self._insert(alert)
            self._set_meta('filament_cursors_v1', cursor)
            return not existed and self.conn.execute('SELECT 1 FROM alerts WHERE id=?', (digest,)).fetchone() is not None

    def enqueue_source(self, alert: Alert) -> bool:
        """Insert once across reconnects and process restarts."""
        with self.conn:
            digest = self.alert_id(alert)
            existed = self.conn.execute('SELECT 1 FROM alerts WHERE id=?', (digest,)).fetchone()
            self._insert(alert)
            return not existed and self.conn.execute('SELECT 1 FROM alerts WHERE id=?', (digest,)).fetchone() is not None

    def enqueue(self, alert: Alert) -> str:
        with self.conn:
            return self._insert(alert).id

    def enqueue_result(self, alert: Alert) -> WriteResult:
        with self.conn:
            return self._insert(alert)

    def unrouted(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute("""SELECT a.* FROM alerts a WHERE NOT EXISTS
            (SELECT 1 FROM alert_routes r WHERE r.alert_id=a.id)
            AND a.delivered IS NULL AND a.suppressed IS NULL
            ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     a.created LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def _finalize_routes(self, alert_id: str) -> None:
        """Finish once every route was delivered or suppressed, never before."""
        counts = self.conn.execute("""SELECT
            SUM(CASE WHEN delivered IS NULL AND suppressed IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN delivered IS NOT NULL THEN 1 ELSE 0 END)
            FROM alert_routes WHERE alert_id=?""", (alert_id,)).fetchone()
        if counts[0]:
            return
        if counts[1]:
            self.conn.execute("""UPDATE alerts
                SET delivered=?, suppressed=NULL, last_error=''
                WHERE id=? AND delivered IS NULL AND suppressed IS NULL""",
                (time.time(), alert_id))
        else:
            self.conn.execute("""UPDATE alerts
                SET suppressed=?, last_error=CASE WHEN EXISTS (SELECT 1 FROM dead_letters d
                    WHERE d.id LIKE alerts.id || ':%') THEN 'permanent failure' ELSE 'suppressed by event policy' END
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
                WHERE r.delivered IS NULL AND r.suppressed IS NULL""").fetchall()
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

    def pending_routes(self, now: float | None = None, limit: int = 20, route: str | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rows = self.conn.execute("""SELECT a.*,r.route FROM alert_routes r
            JOIN alerts a ON a.id=r.alert_id WHERE r.delivered IS NULL
            AND r.suppressed IS NULL AND a.suppressed IS NULL
            AND r.next_retry<=? AND (? IS NULL OR r.route=?)
            AND (a.incident_key IS NULL OR NOT EXISTS (
                SELECT 1 FROM alerts p WHERE p.incident_key=a.incident_key
                AND p.rowid<a.rowid AND p.delivered IS NULL AND p.suppressed IS NULL
                AND (NOT EXISTS (SELECT 1 FROM alert_routes pr WHERE pr.alert_id=p.id)
                    OR EXISTS (SELECT 1 FROM alert_routes pr WHERE pr.alert_id=p.id
                        AND pr.route=r.route AND pr.delivered IS NULL AND pr.suppressed IS NULL))))
            ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     a.created LIMIT ?""", (now, route, route, limit)).fetchall()
        return [dict(r) for r in rows]

    def allow_notification(self, alert_id: str, route: str, kind: str, seconds: float) -> bool:
        """Reserve a rate-limit slot durably; retries of the same item keep their slot."""
        attempt = self.conn.execute(
            "SELECT attempts FROM alert_routes WHERE alert_id=? AND route=?",
            (alert_id, route)).fetchone()
        if attempt is not None and attempt["attempts"] > 0:
            return True
        key = f"notify_limit:{route}:{kind}"
        previous = self.get_meta(key, {})
        now = time.time()
        if previous.get("id") == alert_id:
            return True
        if 0 <= now - previous.get("at", 0) < seconds:
            return False
        self.set_meta(key, {"id": alert_id, "at": now})
        return True

    def route_suppressed(self, alert_id: str, route: str, reason: str) -> None:
        with self.conn:
            self.conn.execute("""UPDATE alert_routes SET suppressed=?,last_error=?
                WHERE alert_id=? AND route=? AND delivered IS NULL AND suppressed IS NULL""",
                (time.time(), reason, alert_id, route))
            self._finalize_routes(alert_id)

    def route_delivered(self, alert_id: str, route: str) -> None:
        with self.conn:
            self.conn.execute("""UPDATE alert_routes SET delivered=?,last_error=''
                WHERE alert_id=? AND route=?""", (time.time(), alert_id, route))
            self._finalize_routes(alert_id)

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
                              (attempts, time.time() + delay, safe_text(error), alert_id, route))

    def pending(self, now: float | None = None, limit: int = 20) -> list[dict]:
        now = time.time() if now is None else now
        rows = self.conn.execute("""SELECT * FROM alerts WHERE delivered IS NULL
            AND suppressed IS NULL AND next_retry<=?
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
                WHERE id=?""", (attempts, time.time() + delay, safe_text(error), alert_id))

    def update_storage_health(self) -> dict:
        size = sum(p.stat().st_size for p in (self.path, Path(str(self.path) + "-wal")) if p.exists())
        free = shutil.disk_usage(self.path.parent).free
        health = {"bytes": size, "disk_free": free,
                  "pressure": size >= 256 * 1024**2 or free < 128 * 1024**2}
        self.set_meta("storage_health", health)
        return health

    def archive_info(self, before: float, limit: int = 500) -> int:
        """Compact terminal G-code records only; keep IDs and route audit."""
        with self.conn:
            rows = self.conn.execute('''SELECT id FROM alerts WHERE kind='notify_gcode_response'
                AND severity='info' AND created<? AND (delivered IS NOT NULL OR suppressed IS NOT NULL)
                AND payload IS NOT NULL LIMIT ?''', (before, limit)).fetchall()
            self.conn.executemany("UPDATE alerts SET payload=NULL,detail='Archived informational G-code' WHERE id=?",
                                  [(row['id'],) for row in rows])
            return len(rows)

    def summary(self) -> dict:
        counts = self.conn.execute("""SELECT
          SUM(CASE WHEN delivered IS NULL AND suppressed IS NULL THEN 1 ELSE 0 END) pending,
          SUM(CASE WHEN delivered IS NOT NULL THEN 1 ELSE 0 END) delivered,
          SUM(CASE WHEN suppressed IS NOT NULL THEN 1 ELSE 0 END) suppressed
          FROM alerts""").fetchone()
        rows = self.conn.execute("""SELECT kind,severity,filename,detail,
            created,delivered,suppressed FROM alerts ORDER BY created DESC LIMIT 10""").fetchall()
        return {"printer": self.printer, "state": self.get_state(),
                "pending": counts["pending"] or 0,
                "delivered": counts["delivered"] or 0,
                "suppressed": counts["suppressed"] or 0,
                "recent": [dict(r) for r in rows]}
