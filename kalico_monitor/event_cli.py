"""Read-only event inspection; never issues printer commands."""
from __future__ import annotations

import argparse
import json
import urllib.request

from .filament_events import EVENTS as FILAMENT_EVENTS
from .monitor_cfg import LIFECYCLE_EVENTS
from .policy import parse_modes
from .setup import default_path, read_env

DESCRIPTIONS = {
    "started": "开始打印", "paused": "暂停打印", "resumed": "恢复打印",
    "complete": "打印完成", "cancelled": "取消打印", "error": "打印异常",
    "stalled": "打印停滞", "print_progress": "定时进度",
    "klippy_shutdown": "Klippy 停机", "klippy_disconnected": "Klippy 断开",
    "klippy_recovered": "Klippy 恢复", "moonraker_unreachable": "Moonraker 断开",
    "moonraker_recovered": "Moonraker 恢复", "nozzle_force_anomaly": "喷嘴力异常",
    **{"filament_" + name: value[0] for name, value in FILAMENT_EVENTS.items()},
    "filament_event_gap": "受力事件流缺失",
    "status_data_stale": "打印机状态数据失效", "status_data_recovered": "打印机状态数据恢复",
}


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("events", "discover", "test", "dead-letters"))
    parser.add_argument("kind", nargs="?")
    parser.add_argument("--retry", metavar="ALERT_ID", help="explicitly replay one dead-letter route")
    parser.add_argument("--route", choices=("direct", "agent"))
    parser.add_argument("--watch", action="store_true", help="observe Moonraker notifications")
    parser.add_argument("--file", type=type(default_path()), default=default_path())
    args = parser.parse_args(argv)
    values = read_env(args.file.expanduser())
    if args.command == "dead-letters":
        from pathlib import Path

        from .policy import routes_for
        from .storage import Outbox

        if args.retry and not args.route:
            parser.error("--retry requires --route")
        path = Path(values["KALICO_MONITOR_DB"]).expanduser()
        if not path.is_file():
            parser.error("Monitor database does not exist")
        store = Outbox(path, values["KALICO_PRINTER_NAME"])
        try:
            if args.retry:
                item = store.conn.execute("""SELECT a.kind FROM alert_routes r JOIN alerts a
                    ON a.id=r.alert_id WHERE r.alert_id=? AND r.route=? AND r.failed_at IS NOT NULL""",
                    (args.retry, args.route)).fetchone()
                if item is None:
                    parser.error("Dead-letter route not found")
                if args.route not in routes_for(item["kind"], parse_modes(values["KALICO_EVENT_MODES"])):
                    parser.error("Route disabled by current event policy")
                if not store.retry_dead_letter(args.retry, args.route):
                    parser.error("Dead-letter route changed")
                print("Route requeued with original request ID; delivery not yet confirmed.")
            else:
                print(json.dumps(store.dead_letters(), ensure_ascii=False, indent=2))
        finally:
            store.close()
        return
    if args.retry or args.route:
        parser.error("--retry and --route are only valid for dead-letters")
    if args.command == "test":
        if args.kind not in LIFECYCLE_EVENTS:
            parser.error("test requires a registered event name")
        import asyncio
        import time
        import uuid

        import aiohttp

        from .notifier import deliver
        from .policy import routes_for
        from .setup import validate_all
        values = validate_all(values)
        routes = routes_for(args.kind, parse_modes(values["KALICO_EVENT_MODES"]))
        if not routes:
            print("事件已关闭，不发送测试通知")
            return
        alert = {"id": "test-" + uuid.uuid4().hex, "kind": args.kind, "severity": "info",
                 "filename": "TEST", "detail": "模拟通知：非真实打印事件", "created": time.time()}
        async def send():
            async with aiohttp.ClientSession(trust_env=False) as session:
                for route in routes:
                    url = values["HERMES_AGENT_WEBHOOK_URL" if route == "agent" else "HERMES_WEBHOOK_URL"]
                    if not url:
                        raise ValueError(f"Missing {route} webhook")
                    result = await deliver(session, url, values["HERMES_WEBHOOK_SECRET"], values["KALICO_PRINTER_NAME"], alert, route)
                    print(route, result.get("status"))
        asyncio.run(send())
        return
    if args.watch:
        if args.command != "events":
            parser.error("--watch is only valid with events")
        import asyncio

        import aiohttp

        from .monitor import subscribe_request
        async def watch():
            url = values["MOONRAKER_URL"].rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/websocket"
            key = values["MOONRAKER_API_KEY"]
            async with aiohttp.ClientSession(trust_env=False) as session:
                async with session.ws_connect(url, headers={"X-Api-Key": key} if key else {}, heartbeat=20) as ws:
                    await ws.send_json(subscribe_request(False))
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            packet = json.loads(message.data)
                            if isinstance(packet, dict) and ("method" in packet or "error" in packet):
                                print(json.dumps(packet, ensure_ascii=False), flush=True)
        try:
            asyncio.run(watch())
        except KeyboardInterrupt:
            pass
        return
    if args.command == "events":
        modes = parse_modes(values["KALICO_EVENT_MODES"])
        for name in LIFECYCLE_EVENTS:
            mode = modes.get(name, modes["default"])
            print(f"{name:25} {mode:7} {DESCRIPTIONS[name]}")
        for name in modes.keys() - set(LIFECYCLE_EVENTS) - {"default"}:
            print(f"{name:25} {modes[name]:7} 未注册，仅配置路由")
        return
    url = values["MOONRAKER_URL"].rstrip("/") + "/printer/objects/list"
    key = values["MOONRAKER_API_KEY"]
    request = urllib.request.Request(url, headers={"X-Api-Key": key} if key else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        result = json.load(response)
    print(json.dumps(result, ensure_ascii=False, indent=2))
