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
    assert result["platforms"]["webhook"]["extra"]["routes"]["kalico-analysis"]["toolsets"] == []
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
