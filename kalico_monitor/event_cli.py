"""Read-only event inspection; never issues printer commands."""
from __future__ import annotations

import argparse
import json
import urllib.request

from .setup import default_path, read_env
from .policy import parse_modes
from .monitor_cfg import LIFECYCLE_EVENTS

DESCRIPTIONS = {
    "started": "开始打印", "paused": "暂停打印", "resumed": "恢复打印",
    "complete": "打印完成", "cancelled": "取消打印", "error": "打印异常",
    "stalled": "打印停滞", "print_progress": "定时进度",
    "klippy_shutdown": "Klippy 停机", "klippy_disconnected": "Klippy 断开",
    "klippy_recovered": "Klippy 恢复", "moonraker_unreachable": "Moonraker 断开",
    "moonraker_recovered": "Moonraker 恢复", "nozzle_force_anomaly": "喷嘴力异常",
}


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("events", "discover"))
    parser.add_argument("--file", type=type(default_path()), default=default_path())
    args = parser.parse_args(argv)
    values = read_env(args.file.expanduser())
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
