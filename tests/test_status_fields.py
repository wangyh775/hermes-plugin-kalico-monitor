"""Configurable status command, optional chamber and ETA regression coverage."""
from __future__ import annotations

import json

import pytest

from kalico_monitor import bootstrap, status
from kalico_monitor.quick import execute, format_status
from kalico_monitor.setup import defaults, read_env, save_env, validate_field
from kalico_monitor.status_fields import DEFAULT_CONFIG, parse_fields, remaining_time


@pytest.fixture
def sample():
    return {
        "print_stats": {
            "state": "printing", "filename": "part.gcode", "print_duration": 3600,
        },
        "webhooks": {"state": "ready"},
        "virtual_sdcard": {"progress": 0.5},
        "extruder": {"temperature": 400, "target": 400},
        "heater_bed": {"temperature": 120, "target": 120},
        "temperature_sensor chamber": {"temperature": 117.3},
        "_kalico_chamber_object": "temperature_sensor chamber",
    }


def test_default_has_eight_expected_fields(sample):
    output = format_status(sample)
    assert output.splitlines() == [
        "🖨️ Kalico · 实时状态",
        "打印：printing｜Klippy：ready",
        "文件：part.gcode",
        "进度：50.0%",
        "喷嘴：400.0°C / 目标 400.0°C",
        "热床：120.0°C / 目标 120.0°C",
        "腔体：117.3°C",
        "已打印：1小时0分钟",
        "预计剩余：1小时0分钟（按文件进度粗略估算）",
    ]
    assert len(parse_fields(DEFAULT_CONFIG)) == 8


def test_selected_fields_are_applied_to_slash_command(tmp_path, monkeypatch, sample):
    env = tmp_path / "monitor.env"
    env.write_text(
        "KALICO_PRINTER_NAME=PEEK-01\n"
        "KALICO_STATUS_FIELDS=state,progress,remaining\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KALICO_MONITOR_ENV_FILE", str(env))
    result = execute("status", query=lambda *_: sample)
    assert result.splitlines() == [
        "🖨️ PEEK-01 · 实时状态",
        "打印：printing｜Klippy：ready",
        "进度：50.0%",
        "预计剩余：1小时0分钟（按文件进度粗略估算）",
    ]
    assert "喷嘴" not in result
    assert "热床" not in result


@pytest.mark.parametrize("raw", [
    "", "state,,file", "state,state", "state,unknown", "status;touch /tmp/x",
])
def test_invalid_fields_rejected(raw):
    with pytest.raises(ValueError):
        validate_field("KALICO_STATUS_FIELDS", raw)


@pytest.mark.parametrize("value", [
    "printer", "extruder", "temperature_sensor /etc/passwd", "gcode_macro chamber",
    "temperature_sensor chamber\nX=1",
])
def test_invalid_chamber_name_rejected(value):
    with pytest.raises(ValueError):
        validate_field("KALICO_CHAMBER_OBJECT", value)


def test_chamber_configuration_round_trip(tmp_path):
    values = defaults()
    values["HERMES_WEBHOOK_SECRET"] = "s" * 64
    values["KALICO_CHAMBER_OBJECT"] = "temperature_sensor chamber"
    values["KALICO_STATUS_FIELDS"] = "state,chamber,remaining"
    env = tmp_path / "monitor.env"
    save_env(env, values)
    result = read_env(env)
    assert result["KALICO_CHAMBER_OBJECT"] == "temperature_sensor chamber"
    assert result["KALICO_STATUS_FIELDS"] == "state,chamber,remaining"


def test_remaining_unavailable_on_idle_or_insufficient_progress(sample):
    idle = dict(sample["print_stats"], state="standby")
    assert "非打印中" in remaining_time(idle, sample["virtual_sdcard"])
    assert "数据不足" in remaining_time(sample["print_stats"], {"progress": 0.01})
    assert "数据不足" in remaining_time(sample["print_stats"], {"progress": True})
    assert "数据不足" in remaining_time({}, {"progress": 0.5})
    assert "腔体：—" in format_status({"print_stats": {"state": "standby"}})


def test_interactive_status_selection_and_cancel():
    choices = iter(["4", "s"])  # remove nozzle while keeping other defaults
    changed = bootstrap.select_status_fields(
        DEFAULT_CONFIG, ask=lambda _: next(choices), out=lambda _: None)
    assert "nozzle" not in parse_fields(changed)
    assert len(parse_fields(changed)) == 7
    cancelled = bootstrap.select_status_fields(
        DEFAULT_CONFIG, ask=lambda _: "0", out=lambda _: None)
    assert cancelled == DEFAULT_CONFIG


def test_reconfigure_menu_stages_field_changes_without_committing():
    values = defaults()
    values["HERMES_WEBHOOK_SECRET"] = "s" * 64
    replies = iter(["12", "4", "s", "11", "y"])
    changed = bootstrap.reconfigure_menu(
        values, ask=lambda _: next(replies), out=lambda _: None)
    assert changed == {"KALICO_STATUS_FIELDS":
                       "state,file,progress,bed,chamber,elapsed,remaining"}
    assert "nozzle" in values["KALICO_STATUS_FIELDS"]


def test_chamber_auto_selects_thermal_object(monkeypatch):
    monkeypatch.setattr(
        status, "_available_objects",
        lambda *_: ["extruder", "temperature_sensor tool",
                    "temperature_sensor chamber", "heater_generic bed"],
    )
    assert status.chamber_object("http://localhost:7125") == "temperature_sensor chamber"
    assert status.chamber_object(
        "http://localhost:7125", configured="heater_generic chamber") == (
            "heater_generic chamber")


def test_chamber_query_uses_status_object_and_is_optional(monkeypatch, sample):
    calls = []

    def fake_query(_url, _key, objects=None):
        calls.append(objects)
        return dict(sample)

    monkeypatch.setattr(status, "query_status", fake_query)
    monkeypatch.setattr(status, "chamber_object", lambda *_: "temperature_sensor chamber")
    result = status.query_status_with_chamber("http://localhost:7125")
    assert "temperature_sensor chamber" in calls[0]
    assert result["_kalico_chamber_object"] == "temperature_sensor chamber"
    calls.clear()
    status.query_status_with_chamber("http://localhost:7125", include_chamber=False)
    assert calls == [None]


def test_status_auto_discovery_error_does_not_break_baseline(monkeypatch):
    monkeypatch.setattr(status, "_available_objects",
                        lambda *_: (_ for _ in ()).throw(OSError("offline")))
    assert status.chamber_object("http://localhost:7125") == ""
