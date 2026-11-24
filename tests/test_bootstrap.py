"""Installer regression tests: route merge, safety, idempotence and rollback."""
from __future__ import annotations

import io
import json

import pytest

from kalico_monitor import bootstrap


def test_preserves_unrelated_hermes_settings_and_home_fallback():
    yaml = bootstrap.yaml_editor()
    config = yaml.load(io.StringIO("""# preserved comment
model:
  provider: existing
platforms:
  feishu:
    enabled: true
  webhook:
    extra:
      host: 127.0.0.1
      routes:
        existing:
          secret: keep-me
          deliver: log
"""))
    result, port = bootstrap.plan_routes(config, "x" * 64, "")
    assert port == 8644
    assert result["model"]["provider"] == "existing"
    assert result["platforms"]["webhook"]["extra"]["routes"]["existing"]["secret"] == "keep-me"
    assert "deliver_extra" not in result["platforms"]["webhook"]["extra"]["routes"]["kalico-alert"]
    assert result["platforms"]["webhook"]["extra"]["routes"]["kalico-analysis"]["toolsets"] == ["kalico_monitor"]
    assert result["platforms"]["webhook"]["extra"]["routes"]["kalico-alert"]["deliver_only"] is True
    result2, _ = bootstrap.plan_routes(result, "x" * 64, "")
    assert result2["model"]["provider"] == "existing"
    output = io.StringIO()
    yaml.dump(result2, output)
    assert "# preserved comment" in output.getvalue()


def test_existing_non_kalico_route_refused():
    config = {"platforms": {"webhook": {"extra": {"routes": {
        "kalico-alert": {"secret": "other-service", "prompt": "unrelated"}
    }}}}}
    with pytest.raises(ValueError, match="already exists"):
        bootstrap.plan_routes(config, "new-key", "")


def test_chat_override_validated(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    assert bootstrap.home_chat(home, {}, "oc_abc123") == "oc_abc123"
    assert bootstrap.home_chat(home, {}, "") == ""
    with pytest.raises(ValueError):
        bootstrap.home_chat(home, {}, "https://evil.test")


def test_configured_home_from_env(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".env").write_text("FEISHU_HOME_CHANNEL=oc_test_group\n")
    assert bootstrap.home_chat(home, {}) == "oc_test_group"


def test_backup_restoration(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("keep: true\n")
    old = path.read_bytes()
    snapshot = bootstrap.backup(path)
    assert snapshot and snapshot.exists() and snapshot.read_bytes() == old
    bootstrap.atomic_write_yaml(path, {"platforms": {"webhook": {"enabled": True}}})
    assert path.read_bytes() != old
    bootstrap.restore(path, old)
    assert path.read_bytes() == old


def test_rejects_symlink_backup(tmp_path):
    real = tmp_path / "real"
    real.write_text("secret")
    link = tmp_path / "config.yaml"
    link.symlink_to(real)
    with pytest.raises(ValueError):
        bootstrap.backup(link)


def test_synthetic_delivery_signature(monkeypatch):
    received = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return b'{"status":"delivered"}'

    class Opener:
        def open(self, request, timeout):
            received["body"] = json.loads(request.data)
            received["headers"] = dict(request.header_items())
            return Response()

    monkeypatch.setattr(bootstrap.urllib.request, "build_opener", lambda *args: Opener())
    bootstrap.test_direct_delivery(8644, "secret", "PEEK-01")
    assert received["body"]["kind"] == "installation_test"
    assert "x-webhook-signature-v2" in {
        key.lower(): value for key, value in received["headers"].items()
    }


def test_failed_plugin_install_rolls_back_configs(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / ".hermes"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text("platforms:\n  feishu:\n    enabled: true\n")
    env = tmp_path / "monitor.env"
    env.write_text("HERMES_WEBHOOK_SECRET=some-existing-very-long-secret\n")
    before_cfg, before_env = cfg.read_bytes(), env.read_bytes()
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda cmd: "/usr/bin/" + cmd)

    def fails(_args, **_kwargs):
        raise RuntimeError("install failed")

    monkeypatch.setattr(bootstrap, "run", fails)
    with pytest.raises(RuntimeError, match="install failed"):
        bootstrap.configure(root, home, "http://192.168.1.100:7125", "PEEK-01",
                            "both", "", env, cfg,
                            manage_service=False, test_connections=False)
    assert cfg.read_bytes() == before_cfg
    assert env.read_bytes() == before_env
    assert list(home.glob("config.yaml.bak.*"))
    assert list(tmp_path.glob("monitor.env.bak.*"))


def test_reconfigure_cancel_never_changes_existing_values():
    current = {
        "MOONRAKER_URL": "http://192.168.1.100:7125",
        "KALICO_PRINTER_NAME": "PEEK-01",
        "KALICO_EVENT_MODES": "klippy_shutdown=both,complete=direct",
    }
    answers = iter(["1", "http://192.168.1.101:7125", "0"])
    result = bootstrap.reconfigure_menu(
        current, ask=lambda _="": next(answers), out=lambda _line: None)
    assert result is None
    assert current["MOONRAKER_URL"] == "http://192.168.1.100:7125"


def test_reconfigure_preserves_other_event_modes():
    from kalico_monitor.setup import defaults

    current = defaults()
    current["HERMES_WEBHOOK_SECRET"] = "x" * 64
    current["KALICO_EVENT_MODES"] = "complete=direct,paused=agent,klippy_shutdown=direct"
    answers = iter(["10", "both", "11", "y"])
    changes = bootstrap.reconfigure_menu(
        current, ask=lambda _="": next(answers), out=lambda _line: None)
    assert changes is not None
    modes = bootstrap.parse_modes(changes["KALICO_EVENT_MODES"])
    assert modes["klippy_shutdown"] == "both"
    assert modes["nozzle_force_anomaly"] == "both"
    assert modes["complete"] == "direct"
    assert modes["paused"] == "agent"
    assert current["KALICO_EVENT_MODES"] == (
        "complete=direct,paused=agent,klippy_shutdown=direct"
    )


def test_reconfigure_declined_confirmation_does_not_write():
    from kalico_monitor.setup import defaults

    current = defaults()
    current["HERMES_WEBHOOK_SECRET"] = "x" * 64
    answers = iter(["2", "PEEK-02", "11", "n", "0"])
    assert bootstrap.reconfigure_menu(
        current, ask=lambda _="": next(answers), out=lambda _line: None) is None
    assert current["KALICO_PRINTER_NAME"] == "PEEK-01"


def test_reconfigure_updates_selected_field_only():
    from kalico_monitor.setup import defaults

    current = defaults()
    current["HERMES_WEBHOOK_SECRET"] = "x" * 64
    current["KALICO_EVENT_MODES"] = "klippy_shutdown=both,paused=agent"
    answers = iter(["1", "http://192.168.1.101:7125", "11", "y"])
    changes = bootstrap.reconfigure_menu(
        current, ask=lambda _="": next(answers), out=lambda _line: None)
    assert changes == {"MOONRAKER_URL": "http://192.168.1.101:7125"}


def test_reconfigure_preserves_existing_feishu_chat_target():
    original = {"platforms": {"webhook": {"extra": {"routes": {
        "kalico-alert": {
            "prompt": bootstrap.DIRECT_PROMPT,
            "deliver_extra": {"chat_id": "oc_prior"}
        },
        "kalico-analysis": {
            "prompt": bootstrap.AGENT_PROMPT,
            "deliver_extra": {"chat_id": "oc_prior"}
        }
    }}}}}
    updated, _port = bootstrap.plan_routes(original, "x" * 64, "")
    routes = updated["platforms"]["webhook"]["extra"]["routes"]
    assert routes["kalico-alert"]["deliver_extra"]["chat_id"] == "oc_prior"
    assert routes["kalico-analysis"]["deliver_extra"]["chat_id"] == "oc_prior"


def test_existing_delivery_target_and_custom_fields_survive_home_channel():
    original = {"platforms": {"webhook": {"extra": {"routes": {
        "kalico-alert": {
            "prompt": bootstrap.DIRECT_PROMPT,
            "deliver_extra": {"chat_id": "oc_existing", "thread_id": "thread"},
            "custom_delivery_option": "retain",
        },
        "kalico-analysis": {
            "prompt": bootstrap.AGENT_PROMPT,
            "deliver_extra": {"chat_id": "oc_existing"},
            "max_turns": 3,
        },
    }}}}}
    result, _ = bootstrap.plan_routes(original, "s" * 64, "oc_new_home")
    routes = result["platforms"]["webhook"]["extra"]["routes"]
    assert routes["kalico-alert"]["deliver_extra"] == {
        "chat_id": "oc_existing", "thread_id": "thread"
    }
    assert routes["kalico-alert"]["custom_delivery_option"] == "retain"
    assert routes["kalico-analysis"]["deliver_extra"]["chat_id"] == "oc_existing"
    assert routes["kalico-analysis"]["max_turns"] == 3
    assert routes["kalico-analysis"]["toolsets"] == ["kalico_monitor"]


def test_reconfigure_only_moonraker_does_not_restart_hermes(tmp_path, monkeypatch):
    from kalico_monitor.setup import defaults, save_env

    root = tmp_path / "plugin"
    root.mkdir()
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    cfg = hermes / "config.yaml"
    config, _ = bootstrap.plan_routes({"platforms": {}}, "s" * 64, "")
    bootstrap.atomic_write_yaml(cfg, config)
    env = tmp_path / "monitor.env"
    values = defaults()
    values["HERMES_WEBHOOK_SECRET"] = "s" * 64
    values["KALICO_EVENT_MODES"] = "klippy_shutdown=both,nozzle_force_anomaly=both"
    save_env(env, values)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: "/usr/bin/mock")
    calls = []
    monkeypatch.setattr(bootstrap, "run", lambda argv, **kwargs: calls.append(argv))
    bootstrap.configure(
        root, hermes, "http://192.168.1.101:7125", "PEEK-01", "both",
        "", env, cfg, manage_service=False, test_connections=False,
        overrides={"MOONRAKER_URL": "http://192.168.1.101:7125"},
        install_plugin=False,
    )
    assert not calls, "Moonraker-only changes must not restart or reinstall Hermes"


def test_plugin_install_keeps_permission_prompt_visible():
    import subprocess

    args = ["hermes", "plugins", "install", "file:///tmp/kalico", "--enable"]
    with pytest.MonkeyPatch.context() as mp:
        calls = []
        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0)
        mp.setattr(bootstrap.subprocess, "run", fake_run)
        bootstrap.run(args, interactive=True, timeout=None)
    assert calls[0][0] == args
    assert "stdout" not in calls[0][1]
    assert "stderr" not in calls[0][1]
    assert calls[0][1]["timeout"] is None


def test_explicit_feishu_change_preserves_other_delivery_fields():
    config = {"platforms": {"webhook": {"extra": {"routes": {
        "kalico-alert": {
            "prompt": bootstrap.DIRECT_PROMPT,
            "deliver_extra": {"chat_id": "oc_old", "thread": "preserve"},
        },
        "kalico-analysis": {"prompt": bootstrap.AGENT_PROMPT},
    }}}}}
    updated, _ = bootstrap.plan_routes(config, "s" * 64, "oc_new", replace_chat=True)
    routes = updated["platforms"]["webhook"]["extra"]["routes"]
    assert routes["kalico-alert"]["deliver_extra"] == {
        "chat_id": "oc_new", "thread": "preserve"
    }
    assert routes["kalico-analysis"]["deliver_extra"]["chat_id"] == "oc_new"


def test_rollback_restores_disabled_unit_and_config(tmp_path, monkeypatch):
    import subprocess

    root = tmp_path / "plugin"
    root.mkdir()
    hermes = tmp_path / ".hermes"
    hermes.mkdir()
    cfg = hermes / "config.yaml"
    cfg.write_text("platforms:\n  feishu:\n    enabled: true\n")
    env = tmp_path / "monitor.env"
    env.write_text("HERMES_WEBHOOK_SECRET=previous-very-long-random-secret\n")
    unit = tmp_path / ".config/systemd/user/kalico-monitor.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("original unit contents\n")
    before = {path: path.read_bytes() for path in (cfg, env, unit)}
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/mock")
    actions = []

    def fake_subprocess(argv, **_kw):
        actions.append(tuple(argv))
        if argv[2] in ("is-active", "is-enabled"):
            return subprocess.CompletedProcess(argv, 1)
        return subprocess.CompletedProcess(argv, 0)

    def fake_run(argv, **_kw):
        actions.append(tuple(argv))
        if argv[-3:] == ["is-active", "--quiet", "kalico-monitor"]:
            raise RuntimeError("simulated service verification failure")
        return subprocess.CompletedProcess(argv, 0)

    restarts = []
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_subprocess)
    monkeypatch.setattr(bootstrap, "run", fake_run)
    monkeypatch.setattr(bootstrap, "restart_gateway", lambda: restarts.append("restart"))
    with pytest.raises(RuntimeError, match="simulated"):
        bootstrap.configure(root, hermes, "http://192.168.1.100:7125",
                            "PEEK-01", "both", "", env, cfg,
                            manage_service=True, test_connections=False)
    for path, original in before.items():
        assert path.read_bytes() == original
    assert ("systemctl", "--user", "stop", "kalico-monitor") in actions
    assert ("systemctl", "--user", "disable", "kalico-monitor") in actions
    assert len(restarts) == 2, "restore the original Hermes config and restart gateway"


@pytest.mark.parametrize("old_key", [
    "", "short", "INSECURE_NO_AUTH", "GENERATE_A_LONG_RANDOM_SECRET"
])
def test_reconfigure_repairs_missing_webhook_secret_on_confirm(old_key):
    from kalico_monitor.setup import defaults

    current = defaults()
    current["HERMES_WEBHOOK_SECRET"] = old_key
    # No field edits: repair still appears in the change summary.
    answers = iter(["11", "y"])
    output = []
    changes = bootstrap.reconfigure_menu(
        current, ask=lambda _="": next(answers), out=output.append,
    )
    assert changes is not None
    assert set(changes) == {"HERMES_WEBHOOK_SECRET"}
    assert len(changes["HERMES_WEBHOOK_SECRET"]) == 64
    assert changes["HERMES_WEBHOOK_SECRET"] != old_key
    assert current["HERMES_WEBHOOK_SECRET"] == old_key
    assert any("自动生成" in item for item in output)
    # The secret must never be echoed into the interactive output.
    assert all(changes["HERMES_WEBHOOK_SECRET"] not in item for item in output)


def test_reconfigure_cancel_or_decline_keeps_missing_secret_unchanged():
    from kalico_monitor.setup import defaults

    current = defaults()
    answers = iter(["11", "n", "0"])
    assert bootstrap.reconfigure_menu(
        current, ask=lambda _="": next(answers), out=lambda _: None,
    ) is None
    assert current["HERMES_WEBHOOK_SECRET"] == ""


def test_existing_valid_secret_is_preserved_without_rotation():
    from kalico_monitor.setup import defaults

    current = defaults()
    current["HERMES_WEBHOOK_SECRET"] = "valid-secret-at-least-16-characters"
    answers = iter(["11"])
    assert bootstrap.reconfigure_menu(
        current, ask=lambda _="": next(answers), out=lambda _: None,
    ) == {}
    assert current["HERMES_WEBHOOK_SECRET"] == "valid-secret-at-least-16-characters"


def test_reconfigure_syncs_generated_key_to_hermes_and_monitor(tmp_path, monkeypatch):
    from kalico_monitor.setup import read_env

    root = tmp_path / "repo"
    root.mkdir()
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text("platforms:\n  feishu:\n    enabled: true\n")
    env_path = tmp_path / "monitor.env"
    env_path.write_text("KALICO_PRINTER_NAME=PEEK-01\nHERMES_WEBHOOK_SECRET=\n")
    current = read_env(env_path)
    choices = iter(["11", "y"])
    changes = bootstrap.reconfigure_menu(
        current, ask=lambda _="": next(choices), out=lambda _: None,
    )
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/mock")
    monkeypatch.setattr(bootstrap, "restart_gateway", lambda: None)
    bootstrap.configure(
        root, hermes_home, current["MOONRAKER_URL"], "PEEK-01", "direct",
        "", env_path, config_path, manage_service=False,
        test_connections=False, overrides=changes, install_plugin=False,
    )
    saved = read_env(env_path)
    hermes = bootstrap.load_hermes(config_path)
    routes = hermes["platforms"]["webhook"]["extra"]["routes"]
    assert saved["HERMES_WEBHOOK_SECRET"] == changes["HERMES_WEBHOOK_SECRET"]
    assert routes["kalico-alert"]["secret"] == saved["HERMES_WEBHOOK_SECRET"]
    assert routes["kalico-analysis"]["secret"] == saved["HERMES_WEBHOOK_SECRET"]


def test_webhook_health_502_is_reported_without_posting(monkeypatch):
    import urllib.error

    calls = []
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    def bad_health(_port):
        calls.append("health")
        raise urllib.error.HTTPError(
            "http://127.0.0.1:8644/health", 502, "Bad Gateway", {}, None
        )

    monkeypatch.setattr(bootstrap, "check_webhook_health", bad_health)
    monkeypatch.setattr(bootstrap, "test_direct_delivery",
                        lambda *_args: calls.append("post"))
    warnings = bootstrap.verify_notification_chain(8644, "secret", "PEEK-01")
    assert len(warnings) == 1
    assert "502" in warnings[0]
    assert "健康检查连续失败" in warnings[0]
    assert calls == ["health"] * 5


def test_transient_502_recovers_without_duplicate_post(monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    def health(_port):
        calls.append("health")
        if calls.count("health") < 3:
            raise RuntimeError("Hermes Webhook 健康检查失败：GET /health 返回 HTTP 502")

    monkeypatch.setattr(bootstrap, "check_webhook_health", health)
    monkeypatch.setattr(bootstrap, "test_direct_delivery",
                        lambda *_args: calls.append("post"))
    assert bootstrap.verify_notification_chain(8644, "secret", "PEEK-01") == []
    assert calls == ["health", "health", "health", "post"]


def test_direct_delivery_http_502_reports_endpoint_without_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "check_webhook_health", lambda _port: None)

    def failed_post(*_args):
        calls.append("post")
        raise RuntimeError("飞书测试通知失败：POST /webhooks/kalico-alert 返回 HTTP 502")

    monkeypatch.setattr(bootstrap, "test_direct_delivery", failed_post)
    warnings = bootstrap.verify_notification_chain(8644, "secret", "PEEK-01")
    assert "POST /webhooks/kalico-alert" in warnings[0]
    assert "502" in warnings[0]
    assert calls == ["post"]


def test_downstream_502_preserves_installed_files(tmp_path, monkeypatch):
    from kalico_monitor.setup import read_env

    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / ".hermes"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("platforms:\n  feishu:\n    enabled: true\n")
    env = tmp_path / "monitor.env"
    env.write_text("HERMES_WEBHOOK_SECRET=\n")
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/mock")
    monkeypatch.setattr(bootstrap, "restart_gateway", lambda: None)
    monkeypatch.setattr(bootstrap, "check_moonraker", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "verify_notification_chain",
                        lambda *_args: ["Hermes Webhook 健康检查失败：GET /health 返回 HTTP 502"])
    warnings = []
    backup_paths = bootstrap.configure(
        root, home, "http://192.168.31.10:7125", "PEEK-01", "both",
        "", env, config, manage_service=False, test_connections=True,
        install_plugin=False, verification_warnings=warnings,
    )
    assert warnings == ["Hermes Webhook 健康检查失败：GET /health 返回 HTTP 502"]
    assert backup_paths
    saved = read_env(env)
    assert saved["MOONRAKER_URL"] == "http://192.168.31.10:7125"
    assert len(saved["HERMES_WEBHOOK_SECRET"]) == 64
    route = bootstrap.load_hermes(config)["platforms"]["webhook"]["extra"]["routes"]
    assert route["kalico-alert"]["secret"] == saved["HERMES_WEBHOOK_SECRET"]


def test_moonraker_preflight_failure_is_fatal_without_config_changes(
    tmp_path, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / ".hermes"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("platforms:\n  feishu:\n    enabled: true\n")
    env = tmp_path / "monitor.env"
    env.write_text("HERMES_WEBHOOK_SECRET=\n")
    original = {path: path.read_bytes() for path in (config, env)}
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/mock")
    monkeypatch.setattr(
        bootstrap, "check_moonraker",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Moonraker HTTP 502")),
    )
    with pytest.raises(RuntimeError, match="Moonraker"):
        bootstrap.configure(
            root, home, "http://192.168.31.10:7125", "PEEK-01", "both",
            "", env, config, manage_service=False, test_connections=True,
            install_plugin=False,
        )
    assert all(path.read_bytes() == content for path, content in original.items())


def test_downstream_502_does_not_disable_new_systemd_user_service(
    tmp_path, monkeypatch
):
    import subprocess

    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / ".hermes"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("platforms:\n  feishu:\n    enabled: true\n")
    env = tmp_path / "monitor.env"
    env.write_text("HERMES_WEBHOOK_SECRET=\n")
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/usr/bin/mock")
    monkeypatch.setattr(bootstrap.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "check_moonraker", lambda *_: None)
    monkeypatch.setattr(bootstrap, "restart_gateway", lambda: None)
    monkeypatch.setattr(bootstrap, "verify_notification_chain",
                        lambda *_: ["Hermes health HTTP 502"])
    systemd_calls = []

    def fake_subprocess(argv, **_kwargs):
        systemd_calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 1)

    def fake_run(argv, **_kwargs):
        systemd_calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_subprocess)
    monkeypatch.setattr(bootstrap, "run", fake_run)
    warnings = []
    bootstrap.configure(
        root, home, "http://192.168.31.10:7125", "PEEK-01", "both",
        "", env, config, manage_service=True, test_connections=True,
        install_plugin=False, verification_warnings=warnings,
    )
    assert warnings == ["Hermes health HTTP 502"]
    assert ("systemctl", "--user", "enable", "--now", "kalico-monitor") in systemd_calls
    assert not any("disable" in row for row in systemd_calls)
    assert (tmp_path / ".config/systemd/user/kalico-monitor.service").exists()



def test_hermes_plugin_state_checks_manifest_and_enabled_list(tmp_path):
    from kalico_monitor import bootstrap

    home = tmp_path / ".hermes"
    home.mkdir()
    cfg = {"plugins": {"enabled": [], "disabled": []}}
    assert bootstrap.hermes_plugin_state(home, cfg) == "missing"
    path = home / "plugins" / "kalico-monitor"
    path.mkdir(parents=True)
    (path / "plugin.yaml").write_text("name: kalico-monitor\\n")
    assert bootstrap.hermes_plugin_state(home, cfg) == "not enabled"
    assert bootstrap.hermes_plugin_state(
        home, {"plugins": {"enabled": ["kalico-monitor"], "disabled": []}}
    ) == "enabled"
    assert bootstrap.hermes_plugin_state(
        home, {"plugins": {"enabled": ["kalico-monitor"], "disabled": ["kalico-monitor"]}}
    ) == "disabled"


def test_reconfigure_reinstalls_missing_plugin_on_confirmation(tmp_path, monkeypatch):
    from kalico_monitor.setup import defaults, save_env

    root = tmp_path / "repo"
    root.mkdir()
    env = tmp_path / "monitor.env"
    values = defaults()
    values["HERMES_WEBHOOK_SECRET"] = "s" * 64
    save_env(env, values)
    hermes = tmp_path / ".hermes"
    hermes.mkdir()
    config = hermes / "config.yaml"
    config.write_text("platforms: {}\\n")
    changed = []
    monkeypatch.setattr(bootstrap, "reconfigure_menu", lambda *_: {})
    monkeypatch.setattr(bootstrap, "configure", lambda *_args, **kwargs: changed.append(kwargs) or [])
    monkeypatch.setattr(bootstrap, "hermes_plugin_state", lambda *_: "missing")
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    monkeypatch.setattr("sys.argv", ["kalico-bootstrap"])
    # Isolate from machine paths using explicit args.
    monkeypatch.setattr(bootstrap.Path, "home", lambda: tmp_path)
    bootstrap.main([
        "--reconfigure", "--hermes-home", str(hermes), "--env-file", str(env),
        "--no-service", "--skip-checks",
    ])
    assert len(changed) == 1
    assert changed[0]["install_plugin"] is True
    assert changed[0]["enable_plugin"] is False


def test_reconfigure_does_not_install_plugin_if_enabled(tmp_path, monkeypatch):
    from kalico_monitor.setup import defaults, save_env

    env = tmp_path / "monitor.env"
    values = defaults()
    values["HERMES_WEBHOOK_SECRET"] = "s" * 64
    save_env(env, values)
    hermes = tmp_path / ".hermes"
    hermes.mkdir()
    (hermes / "config.yaml").write_text("platforms: {}\\n")
    calls = []
    monkeypatch.setattr(bootstrap, "reconfigure_menu", lambda *_: {})
    monkeypatch.setattr(bootstrap, "configure", lambda *_args, **kwargs: calls.append(kwargs) or [])
    monkeypatch.setattr(bootstrap, "hermes_plugin_state", lambda *_: "enabled")
    bootstrap.main([
        "--reconfigure", "--hermes-home", str(hermes), "--env-file", str(env),
        "--no-service", "--skip-checks",
    ])
    assert not calls
