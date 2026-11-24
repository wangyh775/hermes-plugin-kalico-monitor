"""No-LLM command routing, privacy, Moonraker HTTP, SQLite and heartbeat tests."""
import importlib.util
import json
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from kalico_monitor.config import Settings
from kalico_monitor.events import Alert
from kalico_monitor.monitor import Monitor
from kalico_monitor.quick import (
    HELP,
    execute,
    format_status,
    format_temp,
    is_allowed,
    read_monitor,
    setting,
    shared_config,
)
from kalico_monitor.storage import Outbox

SAMPLE = {
    "print_stats": {"state": "printing", "filename": "PEEK_part.gcode"},
    "webhooks": {"state": "ready"},
    "virtual_sdcard": {"progress": 0.685},
    "extruder": {"temperature": 399.23, "target": 400},
    "heater_bed": {"temperature": 119.8, "target": 120.0},
}


def test_status_and_temperature_formatting(monkeypatch):
    monkeypatch.setenv("KALICO_PRINTER_NAME", "PEEK-01")

    def query(_url, _key):
        return SAMPLE

    status = execute("status", query=query)
    assert "68.5%" in status and "399.2°C" in status
    assert "119.8°C" in status
    temp = execute("temp", query=query)
    assert "400.0°C" in temp and "68.5%" not in temp


def test_missing_values_and_non_numeric_progress():
    assert "进度：—" in format_status({"print_stats": {"state": "idle"}})
    assert "异常值" in format_status({"virtual_sdcard": {"progress": 9}})
    assert "喷嘴：—" in format_temp({"extruder": {"temperature": True}})


def test_unknown_commands_do_not_query():
    def forbidden(_url, _key):
        raise AssertionError("Unexpected Moonraker request")

    for cmd in ("help", "cancel", "status extra"):
        assert execute(cmd, query=forbidden) == HELP


def test_shared_config_is_data_not_executable(tmp_path, monkeypatch):
    config = tmp_path / "monitor.env"
    config.write_text(
        "MOONRAKER_URL=http://localhost:7125\n"
        "KALICO_PRINTER_NAME='PEEK 01'\n"
        "UNTRUSTED=$(touch /tmp/not-executed)\n"
        "KALICO_ALLOWED_CHAT_IDS=oc_yes,oc_two\n"
    )
    monkeypatch.setenv("KALICO_MONITOR_ENV_FILE", str(config))
    assert shared_config()["KALICO_PRINTER_NAME"] == "PEEK 01"
    assert "UNTRUSTED" not in shared_config()
    assert setting("MOONRAKER_URL") == "http://localhost:7125"
    assert is_allowed("feishu", {"chat_id": "oc_yes"})
    assert not is_allowed("feishu", {"chat_id": "oc_no"})



def test_shared_config_wins_over_stale_hermes_environment_and_secret(tmp_path, monkeypatch):
    config = tmp_path / "monitor.env"
    config.write_text(
        "MOONRAKER_URL=http://192.168.1.101:7125\n"
        "MOONRAKER_API_KEY=new-key\n"
        "KALICO_PRINTER_NAME=PEEK-NEW\n"
    )
    monkeypatch.setenv("KALICO_MONITOR_ENV_FILE", str(config))
    monkeypatch.setenv("MOONRAKER_URL", "http://192.168.1.100:7125")
    monkeypatch.setenv("MOONRAKER_API_KEY", "old-key")
    def secrets(name):
        return {
            "MOONRAKER_URL": "http://192.168.1.100:7125",
            "MOONRAKER_API_KEY": "old-key",
        }.get(name, "")
    assert setting("MOONRAKER_URL", secrets) == "http://192.168.1.101:7125"
    assert setting("MOONRAKER_API_KEY", secrets) == "new-key"
    assert setting("KALICO_PRINTER_NAME", secrets) == "PEEK-NEW"


def test_gateway_without_verified_identity_is_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("KALICO_MONITOR_ENV_FILE", str(tmp_path / "missing"))
    monkeypatch.delenv("KALICO_ALLOWED_CHAT_IDS", raising=False)
    assert is_allowed("feishu", {"chat_id": "oc_unknown"})
    assert is_allowed("feishu", None)
    assert not is_allowed("", None)
    assert is_allowed("cli", None)
    monkeypatch.setenv("KALICO_ALLOWED_CHAT_IDS", "oc_valid")
    assert is_allowed("feishu", {"chat_id": "oc_valid"})


def test_readonly_alerts_and_health(tmp_path, monkeypatch):
    dbpath = tmp_path / "monitor.sqlite3"
    monkeypatch.setenv("KALICO_MONITOR_DB", str(dbpath))
    assert "尚无监控数据库" in execute("alerts")
    assert "未创建" in execute("health")
    assert not dbpath.exists(), "Read-only requests must not create storage"
    db = Outbox(dbpath, "PEEK-01")
    db.enqueue(Alert(kind="klippy_shutdown", generation=1, filename="p.gcode",
                     detail="SHT36 Thermocouple reader fault", severity="critical"))
    db.set_meta("last_heartbeat_at", 1700000000)
    db.close()
    assert read_monitor(dbpath)["pending"] == 1
    assert "SHT36" in execute("alerts")
    assert "心跳过期" in execute("health", now=1700000200)
    assert "最近心跳 10 秒前" in execute("health", now=1700000010)


def test_monitor_periodic_heartbeat(tmp_path):
    cfg = Settings(
        printer="PEEK-01", moonraker_url="http://localhost:7125", moonraker_api_key="",
        hermes_url="http://localhost:8644/webhooks/kalico-alert", hermes_secret="test",
        sqlite_path=tmp_path / "db.sqlite3", disconnect_seconds=25, stall_minutes=0,
    )
    db = Outbox(cfg.sqlite_path, cfg.printer)
    try:
        monitor = Monitor(cfg, db)
        monitor._heartbeat()
        tick = db.get_meta("last_heartbeat_at")
        assert abs(tick - time.time()) < 5
        monitor._heartbeat()
        assert db.get_meta("last_heartbeat_at") == tick
    finally:
        db.close()


class MoonrakerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        assert self.path == "/printer/objects/query"
        assert "virtual_sdcard" in request["objects"]
        body = json.dumps({"result": {"status": SAMPLE}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@dataclass
class Source:
    chat_id: str


@pytest.mark.asyncio
async def test_plugin_slash_feishu_no_llm(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), MoonrakerHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("MOONRAKER_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("KALICO_ALLOWED_CHAT_IDS", "oc_yes")
    file = Path(__file__).parents[1] / "__init__.py"
    spec = importlib.util.spec_from_file_location("kalico_plugin", file)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = ""
    spec.loader.exec_module(module)

    class Context:
        def __init__(self):
            self.commands = {}
            self.tools = {}

        def register_command(self, name, handler, **_kwargs):
            self.commands[name] = handler

        def register_tool(self, **kwargs):
            self.tools[kwargs["name"]] = kwargs

    ctx = Context()
    module.register(ctx)
    try:
        assert "kalico" in ctx.commands
        assert {"kalico_status", "kalico_monitor_health"} <= set(ctx.tools)
        handler = ctx.commands["kalico"]
        assert "无权查询" in await handler(
            "status", platform="feishu", source=Source("oc_no")
        )
        status = await handler("status", platform="feishu", source=Source("oc_yes"))
        assert "68.5%" in status and "399.2°C" in status
        temp = await handler("temp", platform="feishu", source=Source("oc_yes"))
        assert "400.0°C" in temp
        assert HELP == await handler("help", platform="feishu", source=Source("oc_yes"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
