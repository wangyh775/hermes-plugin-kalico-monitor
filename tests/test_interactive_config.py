"""Interactive config wizard: safety, persistence, defaults, and CLI routing."""
import os
import stat

import pytest

from kalico_monitor.setup import (
    defaults, masked, read_env, save_env, validate_field, wizard,
)


def valid():
    data = defaults()
    data["HERMES_WEBHOOK_SECRET"] = "a" * 64
    return data


def test_atomic_save_preserves_comments_and_unknown_keys(tmp_path):
    path = tmp_path / "monitor.env"
    path.write_text("# retained\nCUSTOM_VALUE=keep\nMOONRAKER_URL=http://old:7125\n")
    save_env(path, valid())
    assert "CUSTOM_VALUE=keep" in path.read_text()
    assert "# retained" in path.read_text()
    assert read_env(path)["MOONRAKER_URL"] == defaults()["MOONRAKER_URL"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_secret_not_echoed_and_cancel_no_write(tmp_path):
    path = tmp_path / "monitor.env"
    outputs = []
    answers = iter(["5", "0"])
    assert wizard(path, ask=lambda _="": next(answers), out=outputs.append) is False
    assert not path.exists()
    assert all("a" * 64 not in line for line in outputs)
    assert masked("HERMES_WEBHOOK_SECRET", "a" * 64).startswith("********")


def test_wizard_save_generated_secret(tmp_path):
    path = tmp_path / "monitor.env"
    outputs = []
    answers = iter(["2", "", "2", "7", "y"])
    assert wizard(path, ask=lambda _="": next(answers), out=outputs.append)
    assert len(read_env(path)["HERMES_WEBHOOK_SECRET"]) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "已保存" in "\n".join(outputs)


@pytest.mark.parametrize("key,value", [
    ("MOONRAKER_URL", "file:///etc/passwd"),
    ("MOONRAKER_URL", "http://host:7125/websocket"),
    ("HERMES_WEBHOOK_URL", "http://192.168.1.1:8644/webhooks/kalico-alert"),
    ("HERMES_WEBHOOK_SECRET", "INSECURE_NO_AUTH"),
    ("HERMES_WEBHOOK_SECRET", "short"),
    ("KALICO_DISCONNECT_SECONDS", "4"),
    ("KALICO_PROGRESS_REPORT_MINUTES", "3"),
    ("KALICO_PROGRESS_REPORT_MINUTES", "1441"),
    ("KALICO_MONITOR_DB", "relative.db"),
    ("KALICO_ALLOWED_CHAT_IDS", "abc;touch /tmp/a"),
    ("KALICO_PRINTER_NAME", "$(touch /tmp/a)"),
])
def test_rejects_invalid_values(key, value):
    with pytest.raises(ValueError):
        validate_field(key, value)


def test_boolean_and_chat_id_normalization():
    assert validate_field("KALICO_WATCH_NOZZLE_FORCE", "yes") == "1"
    assert validate_field("KALICO_ALLOWED_CHAT_IDS", "oc_a, oc_a,oc_b") == "oc_a,oc_b"


def test_invalid_save_does_not_overwrite(tmp_path):
    path = tmp_path / "monitor.env"
    path.write_text("KEEP=untouched\n")
    with pytest.raises(ValueError):
        save_env(path, defaults())
    assert path.read_text() == "KEEP=untouched\n"
