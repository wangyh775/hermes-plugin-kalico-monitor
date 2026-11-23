"""Event routing and durable delivery tests."""
import time

import pytest

from kalico_monitor.config import Settings
from kalico_monitor.policy import parse_modes, routes_for
from kalico_monitor.storage import Outbox
from kalico_monitor.events import Alert
from kalico_monitor.notifier import make_request


def test_policy_defaults_and_overrides():
    modes = parse_modes("klippy_shutdown=both,nozzle_force_anomaly=agent")
    assert routes_for("complete", modes) == ("direct",)
    assert routes_for("klippy_shutdown", modes) == ("direct", "agent")
    assert routes_for("nozzle_force_anomaly", modes) == ("agent",)
    with pytest.raises(ValueError):
        parse_modes("shutdown=unsafe")


def test_each_route_has_separate_retries(tmp_path):
    db = Outbox(tmp_path / "db.sqlite3", "PEEK-01")
    try:
        key = db.enqueue(Alert(kind="klippy_shutdown", generation=8,
                               filename="", severity="critical", detail="fault"))
        db.ensure_routes(key, ("direct", "agent"))
        assert {row["route"] for row in db.pending_routes()} == {"direct", "agent"}
        db.route_delivered(key, "direct")
        assert db.conn.execute("SELECT delivered FROM alerts WHERE id=?", (key,)).fetchone()[0] is None
        db.route_failed(key, "agent", "temporary")
        assert db.pending_routes() == []
        db.conn.execute("UPDATE alert_routes SET next_retry=0 WHERE alert_id=? AND route='agent'", (key,))
        assert [r["route"] for r in db.pending_routes()] == ["agent"]
        db.route_delivered(key, "agent")
        assert db.pending_routes() == []
        assert db.conn.execute("SELECT delivered FROM alerts WHERE id=?", (key,)).fetchone()[0]
        assert not db.unrouted()
    finally:
        db.close()


def test_route_specific_idempotency_ids():
    alert = {"id": "event123", "kind": "complete", "severity": "info",
             "filename": "", "detail": "done", "created": time.time()}
    _, direct = make_request(alert, "secret", "PEEK", timestamp=100, route="direct")
    _, agent = make_request(alert, "secret", "PEEK", timestamp=100, route="agent")
    assert direct["X-Request-ID"] != agent["X-Request-ID"]
    assert direct["X-Webhook-Signature-V2"] == agent["X-Webhook-Signature-V2"]


def test_settings_rejects_remote_plaintext_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_WEBHOOK_SECRET", "safe-secret")
    monkeypatch.setenv("KALICO_MONITOR_DB", str(tmp_path / "db"))
    monkeypatch.setenv("HERMES_AGENT_WEBHOOK_URL", "http://192.168.0.4:8644/webhooks/kalico-analysis")
    with pytest.raises(ValueError):
        Settings.load()
