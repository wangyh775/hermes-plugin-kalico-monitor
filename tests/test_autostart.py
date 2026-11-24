"""No-login boot activation uses systemd Linger, without modifying Hermes."""
from __future__ import annotations

import subprocess

import pytest

from kalico_monitor import autostart


def completed(args, returncode=0, output=""):
    return subprocess.CompletedProcess(args, returncode, stdout=output, stderr="")


def test_preconfigured_linger_does_not_request_privileges(monkeypatch):
    calls = []

    def fake_call(args, **_kwargs):
        calls.append(args)
        if args[:3] == ["systemctl", "--user", "is-enabled"]:
            return completed(args, output="enabled\n")
        if args[:3] == ["systemctl", "--user", "is-active"]:
            return completed(args, output="active\n")
        if args[0] == "loginctl":
            return completed(args, output="Linger=yes\n")
        raise AssertionError(args)

    monkeypatch.setattr(autostart, "_call", fake_call)
    monkeypatch.setattr(autostart, "_require_command", lambda _: None)
    monkeypatch.setattr(autostart, "login_user", lambda: "kalico")
    assert autostart.ensure_boot_autostart() is False
    assert not any("enable-linger" in row for row in calls)


@pytest.mark.parametrize("output,step", [
    ("disabled\n", "is-enabled"),
    ("", "is-active"),
])
def test_inactive_or_disabled_monitor_does_not_change_login_state(
    monkeypatch, output, step
):
    calls = []

    def fake_call(args, **_kwargs):
        calls.append(args)
        if args[2] == "is-enabled":
            return completed(args, returncode=1 if step == "is-enabled" else 0,
                             output=output if step == "is-enabled" else "enabled\n")
        if args[2] == "is-active":
            return completed(args, returncode=1, output="inactive\n")
        raise AssertionError("Must not request or inspect Linger if monitor is not ready")

    monkeypatch.setattr(autostart, "_call", fake_call)
    monkeypatch.setattr(autostart, "_require_command", lambda _: None)
    with pytest.raises(RuntimeError, match="服务"):
        autostart.ensure_boot_autostart()
    assert all("loginctl" not in call for call in calls)


def test_enable_linger_once_after_explicit_authorization(monkeypatch):
    calls = []
    state = {"enabled": False}

    def fake_call(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "systemctl":
            return completed(args, output="enabled\n" if args[2] == "is-enabled" else "active\n")
        if args[0] == "sudo":
            assert args == ["sudo", "loginctl", "enable-linger", "kalico"]
            assert kwargs["interactive"] is True
            state["enabled"] = True
            return completed(args)
        if args[0] == "loginctl":
            return completed(args, output="Linger=yes\n" if state["enabled"] else "Linger=no\n")
        raise AssertionError(args)

    monkeypatch.setattr(autostart, "_call", fake_call)
    monkeypatch.setattr(autostart, "_require_command", lambda _: None)
    monkeypatch.setattr(autostart, "login_user", lambda: "kalico")
    monkeypatch.setattr(autostart.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(autostart.shutil, "which", lambda name: "/usr/bin/" + name)
    assert autostart.ensure_boot_autostart() is True
    assert autostart.ensure_boot_autostart() is False
    assert sum(args[0] == "sudo" for args, _ in calls) == 1
    assert all("hermes" not in " ".join(args) for args, _ in calls)


def test_permission_denied_never_claims_success(monkeypatch):
    def fake_call(args, **_kwargs):
        if args[0] == "systemctl":
            return completed(args, output="enabled\n" if args[2] == "is-enabled" else "active\n")
        if args[0] == "loginctl":
            return completed(args, output="Linger=no\n")
        if args[0] == "sudo":
            return completed(args, returncode=1)
        raise AssertionError(args)

    monkeypatch.setattr(autostart, "_call", fake_call)
    monkeypatch.setattr(autostart, "_require_command", lambda _: None)
    monkeypatch.setattr(autostart, "login_user", lambda: "kalico")
    monkeypatch.setattr(autostart.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(autostart.shutil, "which", lambda _: "/usr/bin/sudo")
    with pytest.raises(RuntimeError, match="无法启用 Linger"):
        autostart.ensure_boot_autostart()


def test_check_mode_is_read_only(monkeypatch):
    calls = []

    def fake_call(args, **_kwargs):
        calls.append(args)
        if args[0] == "systemctl":
            return completed(args, output="enabled\n" if args[2] == "is-enabled" else "active\n")
        return completed(args, output="Linger=no\n")

    monkeypatch.setattr(autostart, "_call", fake_call)
    monkeypatch.setattr(autostart, "_require_command", lambda _: None)
    monkeypatch.setattr(autostart, "login_user", lambda: "kalico")
    with pytest.raises(RuntimeError, match="尚未启用"):
        autostart.ensure_boot_autostart(check_only=True)
    assert not any("enable-linger" in row for row in calls)


def test_root_uses_loginctl_directly(monkeypatch):
    calls = []
    state = {"enabled": False}

    def fake_call(args, **_kwargs):
        calls.append(args)
        if args[0] == "systemctl":
            return completed(args, output="enabled\n" if args[2] == "is-enabled" else "active\n")
        if args[:2] == ["loginctl", "enable-linger"]:
            state["enabled"] = True
            return completed(args)
        return completed(args, output="Linger=yes\n" if state["enabled"] else "Linger=no\n")

    monkeypatch.setattr(autostart, "_call", fake_call)
    monkeypatch.setattr(autostart, "_require_command", lambda _: None)
    monkeypatch.setattr(autostart, "login_user", lambda: "kalico")
    monkeypatch.setattr(autostart.os, "geteuid", lambda: 0)
    assert autostart.ensure_boot_autostart() is True
    assert ["loginctl", "enable-linger", "kalico"] in calls
