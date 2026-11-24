"""Read-only installation health checks; no printer writes, secrets or messages.

An active Monitor daemon does not imply that Hermes installed the slash-command
plugin or that the current Gateway has loaded it.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .bootstrap import check_moonraker, check_webhook_health, hermes_plugin_state, load_hermes
from .setup import read_env, validate_all
from .monitor import subscribe_request


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # PASS, WARN, FAIL
    detail: str


def _command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=8)


def _systemd_state(name: str) -> str:
    result = _command(["systemctl", "--user", name, "kalico-monitor"])
    return result.stdout.strip() if result.returncode == 0 else ""


def _installed_commands(plugin_path: Path) -> tuple[set[str], set[str]]:
    """Inspect the installed copy without executing its Python module."""
    tree = ast.parse((plugin_path / "__init__.py").read_text(encoding="utf-8"))
    commands: set[str] = set()
    tools: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "register_command" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                commands.add(first.value)
        if node.func.attr == "register_tool":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        tools.add(kw.value.value)
    return commands, tools


async def _websocket_probe(url: str, api_key: str) -> None:
    """Open and subscribe to read-only status; never send control commands."""
    pieces = urlsplit(url)
    ws_url = ("wss" if pieces.scheme == "https" else "ws") + "://" + pieces.netloc + "/websocket"
    headers = {"X-Api-Key": api_key} if api_key else {}
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        async with session.ws_connect(ws_url, headers=headers, timeout=6) as ws:
            await ws.send_json(subscribe_request(False))
            for _ in range(8):
                packet = await ws.receive(timeout=6)
                if packet.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(packet.data)
                    if isinstance(data, dict) and data.get("id") == 1:
                        if "error" in data:
                            raise RuntimeError("Moonraker 拒绝状态订阅")
                        if not isinstance(data.get("result"), dict):
                            raise RuntimeError("Moonraker 状态订阅返回无效数据")
                        return
                elif packet.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED,
                                     aiohttp.WSMsgType.CLOSE}:
                    raise RuntimeError("Moonraker 在订阅成功前关闭 WebSocket")
            raise RuntimeError("Moonraker 没有确认状态订阅")


def _git_head(path: Path) -> str:
    if not (path / ".git").exists():
        return ""
    result = _command(["git", "-C", str(path), "rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else ""


def collect(root: Path, hermes_home: Path, env_path: Path,
            *, network: bool = True) -> list[Check]:
    """Produce independent PASS/FAIL items; continue if one subsystem is broken."""
    checks: list[Check] = []
    config: dict | None = None
    values: dict[str, str] | None = None
    if not env_path.is_file():
        checks.append(Check("Monitor 配置", "FAIL", "monitor.env 不存在"))
    else:
        try:
            values = validate_all(read_env(env_path))
        except (OSError, ValueError, KeyError) as exc:
            checks.append(Check("Monitor 配置", "FAIL", f"配置字段无效：{type(exc).__name__}"))
        else:
            checks.append(Check("Monitor 配置", "PASS", "字段完整、签名密钥有效（明文不显示）"))

    try:
        config = load_hermes(hermes_home / "config.yaml")
    except Exception as exc:
        checks.append(Check("Hermes 配置", "FAIL", f"无法读取配置：{type(exc).__name__}"))
    else:
        checks.append(Check("Hermes 配置", "PASS", "config.yaml 可读取"))

    plugin_dir = hermes_home / "plugins" / "kalico-monitor"
    if config is not None:
        try:
            state = hermes_plugin_state(hermes_home, config)
        except (OSError, ValueError, TypeError) as exc:
            state = "invalid"
            checks.append(Check("Hermes 插件", "FAIL", f"状态检查失败：{type(exc).__name__}"))
        if state == "enabled":
            try:
                commands, tools = _installed_commands(plugin_dir)
                expected = {"kalico_status", "kalico_force", "kalico_monitor_health"}
                if "kalico" in commands and expected <= tools:
                    checks.append(Check("Hermes 插件", "PASS",
                                        "已安装且启用；安装副本声明 /kalico 和 3 个只读工具"))
                else:
                    checks.append(Check("Hermes 插件", "FAIL",
                                        "安装副本缺少 /kalico 或必需只读工具的注册代码"))
            except (OSError, SyntaxError, UnicodeError) as exc:
                checks.append(Check("Hermes 插件", "FAIL",
                                    f"无法检查插件注册代码：{type(exc).__name__}"))
        elif state != "invalid":
            checks.append(Check("Hermes 插件", "FAIL",
                                f"插件状态为 {state}；运行 hermes plugins list --plain --no-bundled"))
        if state == "enabled":
            source_rev, installed_rev = _git_head(root), _git_head(plugin_dir)
            if source_rev and installed_rev and source_rev != installed_rev:
                checks.append(Check("Hermes 插件版本", "FAIL",
                                    "Hermes 安装副本与项目 Git HEAD 不同，需要执行插件更新"))
            elif source_rev and installed_rev:
                checks.append(Check("Hermes 插件版本", "PASS", "已安装版本与项目 Git HEAD 一致"))
            else:
                checks.append(Check("Hermes 插件版本", "WARN",
                                    "无法比较 Git 版本；升级后需确认插件同步"))
    else:
        checks.append(Check("Hermes 插件", "FAIL", "无法检查：Hermes 配置不可读"))

    for label, subcommand, expect in (
        ("Monitor 运行", "is-active", "active"),
        ("Monitor 自启", "is-enabled", "enabled"),
    ):
        try:
            actual = _systemd_state(subcommand)
            checks.append(Check(label, "PASS" if actual == expect else "FAIL",
                                actual or f"未达到 {expect}"))
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append(Check(label, "FAIL", f"{type(exc).__name__}"))

    try:
        result = _command(["loginctl", "show-user", str(__import__("getpass").getuser()),
                           "-p", "Linger", "--value"])
        linger = result.stdout.strip() if result.returncode == 0 else ""
        checks.append(Check("无人登录自启", "PASS" if linger.lower() == "yes" else "FAIL",
                            f"Linger={linger or 'unknown'}"))
    except (OSError, subprocess.SubprocessError) as exc:
        checks.append(Check("无人登录自启", "FAIL", f"{type(exc).__name__}"))

    if values is not None:
        database = Path(values["KALICO_MONITOR_DB"])
        if not database.is_file():
            checks.append(Check("Monitor 心跳", "WARN", "SQLite 尚未创建；需检查服务日志"))
        else:
            try:
                from .quick import read_monitor
                state = read_monitor(database)
                heartbeat = state["heartbeat"]
                age = None if heartbeat is None else time.time() - float(heartbeat)
                if age is not None and 0 <= age <= 90:
                    checks.append(Check("Monitor 心跳", "PASS", f"{int(age)} 秒前"))
                else:
                    checks.append(Check("Monitor 心跳", "WARN", "心跳缺失或超过 90 秒"))
            except Exception as exc:
                checks.append(Check("Monitor 心跳", "WARN", f"读取异常：{type(exc).__name__}"))

    if values is not None and config is not None:
        try:
            webhook = config["platforms"]["webhook"]["extra"]
            routes = webhook["routes"]
            secret = values["HERMES_WEBHOOK_SECRET"]
            if (routes["kalico-alert"]["secret"] != secret or
                    routes["kalico-analysis"]["secret"] != secret):
                raise ValueError("Hermes 两条路由与 Monitor 的签名密钥不一致")
            port = int(webhook.get("port", 8644))
            if values["HERMES_WEBHOOK_URL"] != (
                    f"http://127.0.0.1:{port}/webhooks/kalico-alert"):
                raise ValueError("Monitor 的直接通知地址与 Hermes Webhook 监听端口不一致")
            if values["HERMES_AGENT_WEBHOOK_URL"] and (
                    values["HERMES_AGENT_WEBHOOK_URL"] !=
                    f"http://127.0.0.1:{port}/webhooks/kalico-analysis"):
                raise ValueError("Agent Webhook 地址与 Hermes 路由不一致")
            checks.append(Check("Webhook 路由/签名", "PASS", "两条路由密钥与 Monitor 一致"))
        except (KeyError, TypeError, ValueError) as exc:
            port = None
            checks.append(Check("Webhook 路由/签名", "FAIL", str(exc)))
        if network:
            try:
                check_moonraker(values["MOONRAKER_URL"], values["MOONRAKER_API_KEY"])
                checks.append(Check("Moonraker HTTP", "PASS", "GET /server/info 成功"))
            except (OSError, RuntimeError, ValueError) as exc:
                checks.append(Check("Moonraker HTTP", "FAIL", str(exc)))
            try:
                asyncio.run(_websocket_probe(values["MOONRAKER_URL"],
                                             values["MOONRAKER_API_KEY"]))
                checks.append(Check("Moonraker WebSocket", "PASS", "只读状态订阅成功"))
            except (OSError, RuntimeError, ValueError, aiohttp.ClientError,
                    asyncio.TimeoutError) as exc:
                checks.append(Check("Moonraker WebSocket", "FAIL",
                                    f"状态订阅失败：{type(exc).__name__}"))
            if port is not None:
                try:
                    check_webhook_health(port)
                    checks.append(Check("Hermes Gateway", "PASS", "Webhook /health 返回 200"))
                except (OSError, RuntimeError, ValueError) as exc:
                    checks.append(Check("Hermes Gateway", "FAIL", str(exc)))
    if not network:
        checks.append(Check("实时网络检查", "WARN", "已跳过 Moonraker 与 Gateway 的连通性检查"))
    checks.append(Check("飞书与命令端到端", "WARN",
                        "需在飞书实测 /kalico status；自检不会冒充真实消息送达"))
    return checks


def show_checks(checks: list[Check]) -> bool:
    for check in checks:
        print(f"[{check.status}] {check.name}：{check.detail}")
    failures = [check for check in checks if check.status == "FAIL"]
    print(f"自检完成：{len(checks) - len(failures)} 项未失败，{len(failures)} 项失败。"
          "WARN 表示尚未证明通过。")
    return not failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Kalico Monitor + Hermes self-check")
    parser.add_argument("--hermes-home", type=Path, default=Path("~/.hermes"))
    parser.add_argument("--env-file", type=Path, default=Path("~/.config/kalico-monitor/monitor.env"))
    parser.add_argument("--offline", action="store_true", help="Skip HTTP/WebSocket probes")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    result = collect(root, args.hermes_home.expanduser(), args.env_file.expanduser(),
                     network=not args.offline)
    return 0 if show_checks(result) else 2


if __name__ == "__main__":
    raise SystemExit(main())
