"""Regression coverage for read-only installation diagnostics."""
from __future__ import annotations

import pytest

from kalico_monitor import diagnostics


def test_installed_command_inventory_inspects_code_without_import(tmp_path):
    plugin = tmp_path / "kalico-monitor"
    plugin.mkdir()
    (plugin / "__init__.py").write_text(
        "def register(ctx):\n"
        "    ctx.register_command('kalico', handler=None)\n"
        "    ctx.register_tool(name='kalico_status', handler=None)\n"
        "    ctx.register_tool(name='kalico_force', handler=None)\n"
        "    ctx.register_tool(name='kalico_monitor_health', handler=None)\n",
        encoding="utf-8",
    )
    commands, tools = diagnostics._installed_commands(plugin)
    assert commands == {"kalico"}
    assert tools == {"kalico_status", "kalico_force", "kalico_monitor_health"}


def test_inventory_refuses_invalid_python(tmp_path):
    plugin = tmp_path / "kalico-monitor"
    plugin.mkdir()
    (plugin / "__init__.py").write_text("def broken(\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        diagnostics._installed_commands(plugin)


def test_check_reports_missing_plugin_even_when_monitor_is_running(
    tmp_path, monkeypatch
):
    from kalico_monitor.setup import defaults, save_env

    root = tmp_path / "repo"
    root.mkdir()
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("platforms: {}\n", encoding="utf-8")
    env_path = tmp_path / "monitor.env"
    values = defaults()
    values["HERMES_WEBHOOK_SECRET"] = "s" * 64
    save_env(env_path, values)
    monkeypatch.setattr(diagnostics, "_systemd_state",
                        lambda operation: "active" if operation == "is-active" else "enabled")
    monkeypatch.setattr(
        diagnostics, "_command",
        lambda argv: type("Proc", (), {"returncode": 0, "stdout": "yes"})(),
    )
    result = diagnostics.collect(root, hermes_home, env_path, network=False)
    plugin = next(item for item in result if item.name == "Hermes 插件")
    assert plugin.status == "FAIL"
    assert "missing" in plugin.detail
    assert not diagnostics.show_checks(result)


def test_websocket_probe_only_sends_readonly_subscription():
    # Regression: probe must be based on the same status-only subscription as
    # the main monitor, without printer.gcode.script or other write commands.
    from kalico_monitor.monitor import subscribe_request

    req = subscribe_request(False)
    assert req["method"] == "printer.objects.subscribe"
    assert set(req["params"]["objects"]) == {
        "print_stats", "webhooks", "virtual_sdcard", "extruder", "heater_bed"
    }


def test_selfcheck_success_flag_respects_failures():
    assert diagnostics.show_checks([
        diagnostics.Check("x", "PASS", "ok"),
        diagnostics.Check("y", "WARN", "needs real Feishu test"),
    ])
    assert not diagnostics.show_checks([
        diagnostics.Check("x", "FAIL", "not installed"),
    ])
