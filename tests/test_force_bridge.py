"""Legacy nozzle_force_monitor removed; filament_force is the only force source."""
from kalico_monitor.force import format_force
from kalico_monitor.monitor import subscribe_request
from kalico_monitor.monitor_cfg import LIFECYCLE_EVENTS
from kalico_monitor.status import query_force_status


def test_force_subscription_only_new_plugin():
    objects = subscribe_request(True)["params"]["objects"]
    assert objects["filament_force"] == ["events"]
    assert "nozzle_force_monitor" not in objects
    assert "nozzle_force_anomaly" not in LIFECYCLE_EVENTS
    assert "filament_force" not in subscribe_request(False)["params"]["objects"]


def test_force_status_formatter_supports_new_event_journal():
    event = {"kind": "jam_detected", "event_id": "test:1",
             "force_g": 1200, "layer": 2}
    msg = format_force({"filament_force": {"events": [event]}}, "PEEK-01")
    assert "PEEK-01" in msg
    assert "1200 gf" in msg
    assert "未发现 filament_force" in format_force({})


def test_force_query_requests_new_object(monkeypatch):
    from kalico_monitor import status
    calls = []
    def fake_query(url, api_key, timeout, *, objects):
        calls.append(objects)
        return {"filament_force": {"events": []}}
    monkeypatch.setattr(status, "query_status", fake_query)
    assert query_force_status("http://localhost:7125") == {"filament_force": {"events": []}}
    assert calls == [{"filament_force": None}]
