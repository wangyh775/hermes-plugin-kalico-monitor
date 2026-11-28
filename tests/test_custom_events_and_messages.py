"""Configurable Direct notification fields and safe Moonraker event rules."""
import json

import pytest

from kalico_monitor.config import Settings
from kalico_monitor.custom_config import load_extensions, parse_field_list
from kalico_monitor.message_templates import render_direct_message
from kalico_monitor.monitor import Monitor, subscribe_request
from kalico_monitor.monitor_cfg import read_cfg, save_cfg
from kalico_monitor.notifier import make_request
from kalico_monitor.reports import report_fields
from kalico_monitor.rule_engine import RuleEngine
from kalico_monitor.setup import defaults
from kalico_monitor.storage import Outbox

EXTENSIONS = """
[message]
fields: printer,kind,detail

[message filament_jam_confirmed]
fields: printer,layer,force_g,result

[event chamber_temperature_high]
source: moonraker
object: temperature_sensor chamber
field: temperature
condition: gt
threshold: 150
duration: 10
cooldown: 300
severity: warning
mode: both
"""


def make_cfg(tmp_path, extensions=EXTENSIONS):
    cfg = tmp_path / "monitor.cfg"
    cfg.write_text("[events]\ndefault: direct\n" + extensions)
    return cfg


def test_message_field_validation():
    assert parse_field_list("printer,kind,force_g") == ("printer", "kind", "force_g")
    for bad in ("", "printer,printer", "printer,__class__", "printer,{detail}",
                "printer,foo", "printer,,kind"):
        with pytest.raises(ValueError):
            parse_field_list(bad)


def test_extensions_and_wizard_save_preserves_rules(tmp_path):
    cfg = make_cfg(tmp_path)
    messages, rules = load_extensions(cfg)
    assert messages["default"] == ("printer", "kind", "detail")
    assert messages["filament_jam_confirmed"] == ("printer", "layer", "force_g", "result")
    assert rules[0].object == "temperature_sensor chamber"
    assert rules[0].duration == 10 and rules[0].cooldown == 300
    assert read_cfg(cfg)["KALICO_EVENT_MODES"] == "default=direct"
    values = defaults()
    save_cfg(cfg, values)
    assert "[event chamber_temperature_high]" in cfg.read_text()
    assert "[message filament_jam_confirmed]" in cfg.read_text()
    assert load_extensions(cfg)[1] == rules


@pytest.mark.parametrize("extra", [
    "[event foo]\nsource: python\nobject: extruder\nfield: temperature\ncondition: gt\nthreshold: 1\n",
    "[event foo]\nsource: moonraker\nobject: extruder\nfield: temperature\ncondition: gt\nthreshold: NaN\n",
    "[event foo]\nsource: moonraker\nobject: extruder\nfield: temperature\ncondition: gt\nthreshold: 1\nduration: -1\n",
    "[event foo]\nsource: moonraker\nobject: extruder\nfield: temperature\ncondition: exec\nthreshold: 1\n",
    "[event started]\nsource: moonraker\nobject: extruder\nfield: temperature\ncondition: gt\nthreshold: 1\n",
    "[event foo]\nsource: moonraker\nobject: ../etc/passwd\nfield: temperature\ncondition: gt\nthreshold: 1\n",
    "[message foo]\nfields: printer\nshell: rm -rf /\n",
    "[unknown]\nx: 1\n",
])
def test_unsafe_extension_rejected(tmp_path, extra):
    cfg = make_cfg(tmp_path, extra)
    with pytest.raises(ValueError):
        read_cfg(cfg)


def test_direct_templates_event_fields_and_missing_values():
    alert = {"id": "e1", "kind": "filament_jam_confirmed", "severity": "critical",
             "filename": "peek.gcode", "detail": "确诊堵头",
             "created": 1700000000,
             "payload": json.dumps({"layer": 2, "force_g": 3200, "result": "jam"})}
    mapping = {"default": ("printer", "kind", "detail"),
               "filament_jam_confirmed": ("printer", "layer", "force_g", "result")}
    body, _ = make_request(alert, "shared-secret", "PEEK-01", message_fields=mapping)
    payload = json.loads(body)
    assert payload["kind"] == "filament_jam_confirmed"
    assert "设备：PEEK-01" in payload["message"]
    assert "喷嘴力 (gf)：3200" in payload["message"]
    assert "诊断结果：jam" in payload["message"]
    assert "事件：" not in payload["message"]
    missing = render_direct_message(alert, "PEEK-01", ("chamber",))
    assert "腔体温度：—" in missing
    assert "{event}" not in payload["message"]


def test_report_structured_direct_fields():
    status = {"print_stats": {"state": "printing", "filename": "p.gcode",
                              "print_duration": 600},
              "virtual_sdcard": {"progress": 0.5},
              "extruder": {"temperature": 400, "target": 400},
              "heater_bed": {"temperature": 120, "target": 120}}
    payload = report_fields(status)
    assert payload["progress"] == "50.0%"
    assert "400.0" in payload["nozzle"]
    msg = render_direct_message({"kind": "print_progress", "detail": "report",
                                 "id": "1", "created": 1, "payload": payload},
                                "PEEK-01", ("printer", "progress", "nozzle"))
    assert "打印进度：50.0%" in msg


def test_rule_continuous_duration_rearm_cooldown_and_persistence(tmp_path):
    cfg = make_cfg(tmp_path)
    rules = load_extensions(cfg)[1]
    db = Outbox(tmp_path / "db.sqlite", "PEEK-01")
    try:
        engine = RuleEngine(db, rules)
        normal = {"temperature_sensor chamber": {"temperature": 140}}
        hot = {"temperature_sensor chamber": {"temperature": 152}}
        assert engine.evaluate(hot, 100) == 0
        assert engine.evaluate(hot, 109) == 0
        assert engine.evaluate(hot, 110, generation=1) == 1
        assert db.pending()[0]["kind"] == "chamber_temperature_high"
        assert engine.evaluate(hot, 120) == 0
        assert RuleEngine(db, rules).evaluate(hot, 130) == 0
        assert engine.evaluate(normal, 131) == 0
        assert engine.evaluate(hot, 132) == 0
        assert engine.evaluate(hot, 160) == 0  # cooldown
        assert engine.evaluate(normal, 401) == 0
        assert engine.evaluate(hot, 402) == 0
        assert engine.evaluate(hot, 412) == 1
        assert len(db.pending()) == 2
        assert engine.evaluate(hot, 413, connected=False) == 0
        assert engine.evaluate(hot, 414) == 0
        assert engine.evaluate(hot, 424) == 0  # already fired in same window
    finally:
        db.close()


def test_no_alarm_from_missing_stale_or_boolean_readings(tmp_path):
    rule = load_extensions(make_cfg(tmp_path))[1]
    db = Outbox(tmp_path / "db.sqlite", "PEEK-01")
    try:
        engine = RuleEngine(db, rule)
        assert engine.evaluate({"temperature_sensor chamber": {"temperature": True}}, 10) == 0
        assert engine.evaluate({"temperature_sensor chamber": {"temperature": float("nan")}}, 100) == 0
        assert engine.evaluate({"temperature_sensor chamber": {"temperature": 500}}, 110, connected=False) == 0
        assert db.pending() == []
    finally:
        db.close()


def test_custom_mode_inherits_rule_unless_explicit_event_override(tmp_path):
    rules = load_extensions(make_cfg(tmp_path))[1]
    kwargs = dict(printer="PEEK", moonraker_url="http://localhost:7125",
                  moonraker_api_key="", hermes_url="http://localhost:8644/webhooks/alert",
                  hermes_secret="secret", sqlite_path=tmp_path / "db.sqlite",
                  disconnect_seconds=25, stall_minutes=0, event_rules=rules)
    db = Outbox(kwargs["sqlite_path"], "PEEK")
    try:
        m = Monitor(Settings(**kwargs), db)
        assert m.event_modes["chamber_temperature_high"] == "both"
        m2 = Monitor(Settings(**{**kwargs, "event_modes": "chamber_temperature_high=off"}), db)
        assert m2.event_modes["chamber_temperature_high"] == "off"
        assert subscribe_request(False, extra_objects=("temperature_sensor chamber",))[
            "params"]["objects"]["temperature_sensor chamber"] is None
    finally:
        db.close()


def test_threshold_edit_restarts_observation_window(tmp_path):
    from dataclasses import replace
    rules = load_extensions(make_cfg(tmp_path))[1]
    db = Outbox(tmp_path / "restart.sqlite", "PEEK")
    try:
        hot = {"temperature_sensor chamber": {"temperature": 180}}
        a = RuleEngine(db, rules)
        assert a.evaluate(hot, 100) == 0
        new_rule = replace(rules[0], threshold=160)
        b = RuleEngine(db, (new_rule,))
        assert b.evaluate(hot, 109) == 0
        assert b.evaluate(hot, 119) == 1
    finally:
        db.close()


def test_subscription_fallback_preserves_base_monitoring(tmp_path):
    rules = load_extensions(make_cfg(tmp_path))[1]
    cfg = Settings(printer="PEEK", moonraker_url="http://localhost:7125",
                   moonraker_api_key="", hermes_url="http://localhost:8644/webhooks/alert",
                   hermes_secret="secret", sqlite_path=tmp_path / "sub.sqlite",
                   disconnect_seconds=25, stall_minutes=0, event_rules=rules,
                   watch_nozzle_force=True)
    db = Outbox(cfg.sqlite_path, cfg.printer)
    try:
        subs = Monitor(cfg, db)._subscriptions(("temperature_sensor chamber",))
        first = subs[0]["params"]["objects"]
        assert "temperature_sensor chamber" in first
        assert first["filament_force"] == ["events"]
        assert "nozzle_force_monitor" not in first
        assert subs[-1]["params"]["objects"] == subscribe_request(False)["params"]["objects"]
    finally:
        db.close()


def test_single_cfg_parse_and_independent_direct_template(tmp_path, monkeypatch):
    from kalico_monitor import custom_config
    from kalico_monitor.monitor_cfg import load_cfg

    path = tmp_path / "monitor.cfg"
    path.write_text("[events]\npaused: direct\n[message paused]\ntemplate: Pause {printer}\n")
    original = custom_config._parser
    calls = []

    def counted(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(custom_config, "_parser", counted)
    values, sections = load_cfg(path)
    _, _, handlers, _, templates = sections
    assert calls == [path]
    assert values["KALICO_EVENT_MODES"] == "paused=direct"
    assert handlers.handlers == {}
    assert templates == {"paused": "Pause {printer}"}
