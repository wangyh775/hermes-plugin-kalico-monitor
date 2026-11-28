"""Read-only event inspection; never issues printer commands."""
from __future__ import annotations

import argparse
import json
import urllib.request

from .custom_config import load_sections
from .event_registry import NATIVE, REGISTRY, SOURCE_TYPES, definition
from .handlers import agent_url
from .monitor_cfg import cfg_path
from .policy import mode_for, parse_modes
from .setup import default_path, read_env


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("events", "discover", "test", "handlers"))
    parser.add_argument("kind", nargs="?")
    parser.add_argument("--source", choices=SOURCE_TYPES)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--info")
    parser.add_argument("--watch", action="store_true", help="observe Moonraker notifications")
    parser.add_argument("--file", type=type(default_path()), default=default_path())
    args = parser.parse_args(argv)
    values = read_env(args.file.expanduser())
    path = cfg_path(args.file.expanduser())
    message_fields, custom_rules, handlers, _, templates = load_sections(path)
    if args.command == "handlers":
        if args.watch or args.native or args.source:
            parser.error("invalid handler filter")
        if args.info:
            handler = handlers.handlers.get(args.info)
            if not handler:
                parser.error("unknown handler")
            print(json.dumps({"name": handler.name, "skills": handler.skills,
                              "route": handler.route, "prompt": handler.prompt},
                             ensure_ascii=False, indent=2))
        else:
            for handler in handlers.handlers.values():
                bound = [kind for kind, name in handlers.bindings.items() if name == handler.name]
                print(f"{handler.name:24} {handler.route}  {','.join(bound) or 'unbound'}")
            if not handlers.handlers:
                print("未定义 Handler；Agent 使用默认分析路由")
        return
    if (args.source or args.native or args.info) and (args.command != "events" or args.watch):
        parser.error("event filters require events without --watch")
    if args.command == "test":
        entry = definition(args.kind, custom_rules)
        if not entry or not entry.forwardable:
            parser.error("test requires a registered event name")
        import asyncio
        import time
        import uuid

        import aiohttp

        from .notifier import deliver
        from .policy import routes_for
        from .setup import validate_all
        values = validate_all(values)
        modes = parse_modes(values["KALICO_EVENT_MODES"])
        for rule in custom_rules:
            if rule.name not in modes:
                modes[rule.name] = rule.mode
        routes = routes_for(args.kind, modes)
        if not routes:
            print("事件已关闭，不发送测试通知")
            return
        alert = {"id": "test-" + uuid.uuid4().hex, "kind": args.kind, "severity": "info",
                 "filename": "TEST", "detail": "模拟通知：非真实打印事件", "created": time.time()}
        async def send():
            async with aiohttp.ClientSession(trust_env=False) as session:
                for route in routes:
                    url = values["HERMES_AGENT_WEBHOOK_URL" if route == "agent" else "HERMES_WEBHOOK_URL"]
                    if route == "agent":
                        url = agent_url(url, handlers.for_event(args.kind))
                    if not url:
                        raise ValueError(f"Missing {route} webhook")
                    result = await deliver(session, url, values["HERMES_WEBHOOK_SECRET"],
                                           values["KALICO_PRINTER_NAME"], alert, route,
                                           message_fields=message_fields, message_templates=templates)
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
        for rule in custom_rules:
            modes.setdefault(rule.name, rule.mode)
        entries = {**REGISTRY, **{r.name: definition(r.name, custom_rules) for r in custom_rules}}
        if args.info:
            entry = entries.get(args.info)
            if not entry:
                parser.error("unknown event")
            handler = handlers.for_event(entry.kind)
            print(json.dumps({**vars(entry), "mode": mode_for(entry.kind, modes),
                              "handler": handler.name if handler else None},
                             ensure_ascii=False, indent=2))
            return
        for source in SOURCE_TYPES:
            if args.source and source != args.source:
                continue
            selected = [entry for entry in entries.values() if entry.source_type == source
                        and (not args.native or entry.kind in NATIVE)]
            if not selected:
                continue
            print(f"[{source}]")
            for entry in selected:
                mode = mode_for(entry.kind, modes) if entry.forwardable else "catalog"
                print(f"{entry.kind:34} {mode:7} {entry.description}")
        if not args.native and not args.source:
            for name in sorted(modes.keys() - entries.keys() - {"default"}):
                print(f"{name:34} {modes[name]:7} 未注册，仅配置路由")
        return
    url = values["MOONRAKER_URL"].rstrip("/") + "/printer/objects/list"
    key = values["MOONRAKER_API_KEY"]
    request = urllib.request.Request(url, headers={"X-Api-Key": key} if key else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        result = json.load(response)
    print(json.dumps(result, ensure_ascii=False, indent=2))
