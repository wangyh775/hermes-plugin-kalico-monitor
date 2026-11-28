from pathlib import Path

import pytest

from kalico_monitor.events import Alert
from kalico_monitor.monitor_cfg import read_cfg, save_cfg
from kalico_monitor.policy import parse_modes, routes_for
from kalico_monitor.setup import defaults, read_env
from kalico_monitor.storage import Outbox


def test_cfg_roundtrip(tmp_path: Path):
    values = defaults()
    values["KALICO_EVENT_MODES"] = "default=direct,paused=off,resumed=both"
    values["KALICO_PROGRESS_REPORT_MINUTES"] = "15"
    path = tmp_path / "monitor.cfg"
    save_cfg(path, values)
    actual = read_cfg(path)
    assert actual["KALICO_PROGRESS_REPORT_MINUTES"] == "15"
    assert parse_modes(actual["KALICO_EVENT_MODES"]) == parse_modes(values["KALICO_EVENT_MODES"])
    assert "HERMES_WEBHOOK_SECRET" not in path.read_text()
    assert read_env(tmp_path / "monitor.env")["KALICO_PROGRESS_REPORT_MINUTES"] == "15"


@pytest.mark.parametrize("content", [
    "[events]\npaused: unknown\n",
    "[monitor]\nprogress_report_minutes: 2\n",
    "[events]\npaused: direct\npaused: off\n",
])
def test_invalid_cfg(tmp_path, content):
    path = tmp_path / "monitor.cfg"
    path.write_text(content)
    with pytest.raises(ValueError):
        read_cfg(path)


def test_off_does_not_retry(tmp_path):
    store = Outbox(tmp_path / "test.sqlite3", "test")
    try:
        alert_id = store.enqueue(Alert("paused", 1, "test.gcode", "test", "info"))
        assert routes_for("paused", parse_modes("paused=off")) == ()
        store.ensure_routes(alert_id, ())
        assert not store.unrouted()
        assert not store.pending_routes()
    finally:
        store.close()
