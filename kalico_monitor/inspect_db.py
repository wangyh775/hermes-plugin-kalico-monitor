"""Read-only diagnostic queries. No replay, deletion or printer control."""
from __future__ import annotations

import argparse
import json
import sqlite3

from .config import Settings


def inspect(path, group, action, event_id=None):
    with sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=1) as conn:
        conn.row_factory = sqlite3.Row
        if group == "dead-letters":
            if action == "show":
                if not event_id:
                    raise ValueError("event_id required")
                rows = conn.execute("SELECT * FROM dead_letters WHERE id=?", (event_id,))
            else:
                rows = conn.execute("SELECT id,kind,route,reason,created FROM dead_letters ORDER BY created DESC LIMIT 100")
        elif group == "incidents":
            rows = conn.execute("SELECT id,kind,incident_key,severity,created,delivered,suppressed FROM alerts WHERE incident_key IS NOT NULL ORDER BY created DESC LIMIT 100")
        else:
            rows = conn.execute("SELECT key,value FROM metadata WHERE key IN ('storage_health','rpc_health','subscription_health')")
        return [dict(row) for row in rows]


def main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=("dead-letters", "incidents", "storage"))
    parser.add_argument("action", choices=("list", "show", "status"))
    parser.add_argument("event_id", nargs="?")
    parsed = parser.parse_args(args)
    if parsed.action not in {"dead-letters": ("list", "show"), "incidents": ("list",), "storage": ("status",)}[parsed.group]:
        parser.error("unsupported action")
    settings = Settings.load()
    try:
        print(json.dumps(inspect(settings.sqlite_path, parsed.group, parsed.action, parsed.event_id),
                         ensure_ascii=True, indent=2))
    except (sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
