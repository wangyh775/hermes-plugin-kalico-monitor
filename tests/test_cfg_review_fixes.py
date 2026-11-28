"""Regression coverage for PR #19 configuration and delivery review fixes."""
from pathlib import Path

import pytest

from kalico_monitor import bootstrap, setup
from kalico_monitor.events import Alert
from kalico_monitor.monitor_cfg import read_cfg, save_cfg
from kalico_monitor.policy import parse_modes, routes_for
from kalico_monitor.quick import format_alerts, read_monitor
from kalico_monitor.setup import defaults, read_env, save_local_config
from kalico_monitor.storage import Outbox


def test_generated_cfg_does_not_pin_implicit_lifecycle_modes(tmp_path):
    values = defaults()
    path = tmp_path / "monitor.cfg"
    save_cfg(path, values)
    content = path.read_text()
    assert "default: direct" in content
    assert "started:" not in content
    assert "resumed:" not in content
    path.write_text(content.replace("default: direct", "default: off"))
    modes = parse_modes(read_cfg(path)["KALICO_EVENT_MODES"])
    assert routes_for("started", modes) == ()
    assert routes_for("resumed", modes) == ()


def test_off_policy_suppresses_queued_direct_and_agent_retries(tmp_path):
    store = Outbox(tmp_path / "alerts.sqlite3", "PEEK-01")
    try:
        key = store.enqueue(Alert("paused", 1, "print.gcode", "paused", "info"))
        store.ensure_routes(key, ("direct", "agent"))
        assert store.reconcile_policy(parse_modes("paused=off")) == 2
        assert store.pending_routes() == []
        assert store.unrouted() == []
        totals = store.summary()
        assert totals["pending"] == 0
        assert totals["delivered"] == 0
        assert totals["suppressed"] == 1
        row = store.conn.execute(
            "SELECT delivered,suppressed,last_error FROM alerts WHERE id=?", (key,)
        ).fetchone()
        assert row["delivered"] is None
        assert row["suppressed"] is not None
        assert row["last_error"] == "suppressed by event policy"
        store.conn.execute(
            "UPDATE alerts SET next_retry=0 WHERE id=?", (key,)
        )
        assert store.pending() == []
    finally:
        store.close()
    recent = read_monitor(tmp_path / "alerts.sqlite3")
    assert recent["pending"] == 0
    assert "已过滤" in format_alerts(recent)


def test_routing_change_preserves_real_delivery_and_cancels_only_agent(tmp_path):
    store = Outbox(tmp_path / "alerts.sqlite3", "PEEK-01")
    try:
        key = store.enqueue(Alert("paused", 1, "print.gcode", "paused", "info"))
        store.ensure_routes(key, ("direct", "agent"))
        store.route_delivered(key, "direct")
        assert store.reconcile_policy(parse_modes("paused=direct")) == 1
        assert not store.pending_routes()
        totals = store.summary()
        assert totals["pending"] == 0
        assert totals["delivered"] == 1
        assert totals["suppressed"] == 0
        rows = store.conn.execute(
            "SELECT route,delivered,suppressed FROM alert_routes WHERE alert_id=?", (key,)
        ).fetchall()
        state = {row["route"]: row for row in rows}
        assert state["direct"]["delivered"] is not None
        assert state["agent"]["suppressed"] is not None
    finally:
        store.close()


def test_old_suppressed_records_migrate_without_false_delivery(tmp_path):
    path = tmp_path / "alerts.sqlite3"
    store = Outbox(path, "PEEK-01")
    try:
        key = store.enqueue(Alert("paused", 1, "", "paused", "info"))
        store.conn.execute(
            "UPDATE alerts SET delivered=123, suppressed=NULL, "
            "last_error='suppressed by event policy' WHERE id=?", (key,)
        )
        store.conn.commit()
    finally:
        store.close()
    restored = Outbox(path, "PEEK-01")
    try:
        assert restored.summary()["suppressed"] == 1
        assert restored.summary()["delivered"] == 0
    finally:
        restored.close()


def test_save_local_pair_rolls_back_on_second_file_failure(tmp_path, monkeypatch):
    env = tmp_path / "monitor.env"
    cfg = tmp_path / "monitor.cfg"
    values = defaults()
    values["HERMES_WEBHOOK_SECRET"] = "x" * 64
    save_local_config(env, values)
    before = {path: path.read_bytes() for path in (env, cfg)}
    changed = dict(values)
    changed["KALICO_PROGRESS_REPORT_MINUTES"] = "30"

    def failing_env_save(path, new_values):
        path.write_text("PARTIALLY_WRITTEN=1")
        raise OSError("simulated storage failure")

    monkeypatch.setattr(setup, "save_env", failing_env_save)
    with pytest.raises(OSError, match="storage failure"):
        save_local_config(env, changed)
    assert {path: path.read_bytes() for path in (env, cfg)} == before


def test_reconfigure_can_clear_explicit_feishu_group():
    values = defaults()
    values["HERMES_WEBHOOK_SECRET"] = "s" * 64
    values["KALICO_DIRECT_CHAT_ID"] = "oc_previous"
    answers = iter(["15", "-", "11", "y"])
    changes = bootstrap.reconfigure_menu(
        values, ask=lambda _="": next(answers), out=lambda _: None
    )
    assert changes is not None
    assert changes["KALICO_DIRECT_CHAT_ID"] == ""
    assert values["KALICO_DIRECT_CHAT_ID"] == "oc_previous"


def test_apply_explicit_empty_chat_target_clears_old_route(tmp_path, monkeypatch):
    root = tmp_path / "plugin"
    root.mkdir()
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config = {"platforms": {"feishu": {"home_channel": "oc_home"}}}
    config, _ = bootstrap.plan_routes(config, "s" * 64, "oc_previous")
    routes = config["platforms"]["webhook"]["extra"]["routes"]
    routes["kalico-alert"]["deliver_extra"]["thread_id"] = "preserve"
    bootstrap.atomic_write_yaml(config_path, config)
    env = tmp_path / "monitor.env"
    env.write_text("HERMES_WEBHOOK_SECRET=" + "s" * 64 + "\n")
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/fake")
    monkeypatch.setattr(bootstrap, "restart_gateway", lambda: None)
    bootstrap.configure(
        root, hermes_home, "http://localhost:7125", "PEEK-01",
        "direct", "", env, config_path, manage_service=False,
        test_connections=False, overrides={
            "KALICO_DIRECT_CHAT_ID": "",
            "KALICO_AGENT_CHAT_ID": "",
        }, install_plugin=False,
    )
    after = bootstrap.load_hermes(config_path)
    routes = after["platforms"]["webhook"]["extra"]["routes"]
    assert routes["kalico-alert"]["deliver_extra"] == {"thread_id": "preserve"}
    assert "deliver_extra" not in routes["kalico-analysis"]
    saved = read_env(env)
    assert saved["KALICO_DIRECT_CHAT_ID"] == ""
    assert saved["KALICO_AGENT_CHAT_ID"] == ""
