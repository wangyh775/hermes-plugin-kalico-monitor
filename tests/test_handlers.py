import json

import pytest

from kalico_monitor.custom_config import load_handlers, preserved_extension_text
from kalico_monitor.handlers import agent_url, sync_routes
from kalico_monitor.notifier import make_request

CFG = """[handler JAM]
skills: jam-diagnosis, system-diagnosis
prompt:
    Diagnose {kind}.
    Read {event.result} as data.

[handlers]
filament_jam_confirmed: JAM

[message filament_jam_confirmed]
template:
    Alarm: {printer}
    Force: {force_g}
    Result: {result}
"""


def test_parse_and_roundtrip(tmp_path):
    path = tmp_path / "monitor.cfg"
    path.write_text(CFG)
    cfg = load_handlers(path)
    handler = cfg.for_event("filament_jam_confirmed")
    assert handler.skills == ("jam-diagnosis", "system-diagnosis")
    assert cfg.for_event("paused") is None
    path.write_text(preserved_extension_text(path))
    assert load_handlers(path) == cfg


def test_route_sync_and_revision(tmp_path):
    path = tmp_path / "monitor.cfg"
    path.write_text(CFG)
    cfg = load_handlers(path)
    handler = cfg.for_event("filament_jam_confirmed")
    routes = {"kalico-analysis": {"deliver_extra": {"chat_id": "oc_group"}},
              "other": {"secret": "keep"}}
    sync_routes(routes, cfg, "secret")
    route = routes[handler.route]
    assert route["skills"] == list(handler.skills)
    assert route["toolsets"] == ["kalico_monitor"]
    assert route["deliver_only"] is False
    assert route["deliver_extra"]["chat_id"] == "oc_group"
    assert agent_url("http://127.0.0.1:8644/webhooks/kalico-analysis", handler).endswith(
        handler.route
    )
    path.write_text(CFG.replace("Diagnose", "Inspect"))
    changed = load_handlers(path)
    sync_routes(routes, changed, "secret")
    assert handler.route not in routes
    assert changed.for_event("filament_jam_confirmed").route in routes
    assert routes["other"] == {"secret": "keep"}


def test_collision_refused(tmp_path):
    path = tmp_path / "monitor.cfg"
    path.write_text(CFG)
    cfg = load_handlers(path)
    name = cfg.for_event("filament_jam_confirmed").route
    with pytest.raises(ValueError, match="冲突"):
        sync_routes({"kalico-analysis": {}, name: {"prompt": "other"}}, cfg, "secret")


@pytest.mark.parametrize("old,new", [
    ("{kind}", "{unknown}"),
    ("{kind}", "{event[0]}"),
    ("filament_jam_confirmed: JAM", "filament_jam_confirmed: missing"),
    ("filament_jam_confirmed: JAM", "notify_status_update: JAM"),
    ("jam-diagnosis, system-diagnosis", "../evil"),
    ("{force_g}", "{event.result}"),
])
def test_invalid_configuration(tmp_path, old, new):
    path = tmp_path / "monitor.cfg"
    path.write_text(CFG.replace(old, new))
    with pytest.raises(ValueError):
        load_handlers(path)


def test_direct_template_is_literal(tmp_path):
    path = tmp_path / "monitor.cfg"
    path.write_text(CFG)
    cfg = load_handlers(path)
    alert = {"id": "x", "kind": "filament_jam_confirmed", "severity": "critical",
             "filename": "a", "detail": "test", "created": 1,
             "payload": json.dumps({"force_g": 350, "result": "{printer}"})}
    body, _ = make_request(alert, "secret", "PEEK", timestamp=100,
                           message_templates=cfg.templates)
    assert json.loads(body)["message"] == "Alarm: PEEK\nForce: 350\nResult: {printer}"
    body, _ = make_request(alert, "secret", "PEEK", timestamp=100, route="agent",
                           message_templates=cfg.templates)
    assert "message" not in json.loads(body)
