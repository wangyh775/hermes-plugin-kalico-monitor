"""Local interactive configuration wizard; no printer writes or LLM."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .policy import parse_modes
from .status_fields import DEFAULT_CONFIG, parse_fields

KEYS = (
    "KALICO_PRINTER_NAME", "MOONRAKER_URL", "MOONRAKER_API_KEY",
    "HERMES_WEBHOOK_URL", "HERMES_WEBHOOK_SECRET", "KALICO_MONITOR_DB",
    "KALICO_ALLOWED_CHAT_IDS", "KALICO_DISCONNECT_SECONDS",
    "KALICO_STALL_MINUTES", "KALICO_PROGRESS_REPORT_MINUTES",
    "KALICO_WATCH_NOZZLE_FORCE", "HERMES_AGENT_WEBHOOK_URL", "KALICO_EVENT_MODES",
    "KALICO_STATUS_FIELDS", "KALICO_CHAMBER_OBJECT",
    "KALICO_DIRECT_CHAT_ID", "KALICO_AGENT_CHAT_ID",
)
SECRET_KEYS = frozenset({"MOONRAKER_API_KEY", "HERMES_WEBHOOK_SECRET"})
PLACEHOLDERS = frozenset({"GENERATE_A_LONG_RANDOM_SECRET", "INSECURE_NO_AUTH"})
GROUPS = {
    "1": ("打印机与 Moonraker", KEYS[:3]),
    "2": ("Hermes Webhook", KEYS[3:5]),
    "3": ("数据库与会话授权", KEYS[5:7]),
    "4": ("监控与通知", KEYS[7:11]),
    "8": ("LLM 分析与事件策略", KEYS[11:13]),
    "9": ("状态命令显示字段", KEYS[13:15]),
}
LABELS = {
    "KALICO_DIRECT_CHAT_ID": "直推飞书会话 ID",
    "KALICO_AGENT_CHAT_ID": "分析飞书会话 ID",
    "KALICO_PRINTER_NAME": "打印机名称",
    "MOONRAKER_URL": "Moonraker 地址",
    "MOONRAKER_API_KEY": "Moonraker API Key（可选）",
    "HERMES_WEBHOOK_URL": "Hermes Webhook 地址",
    "HERMES_WEBHOOK_SECRET": "Hermes Webhook 密钥",
    "KALICO_MONITOR_DB": "SQLite 数据库绝对路径",
    "KALICO_ALLOWED_CHAT_IDS": "允许查询的会话 ID（逗号分隔）",
    "KALICO_DISCONNECT_SECONDS": "断线确认秒数（至少 5）",
    "KALICO_STALL_MINUTES": "停滞检测分钟数（0 关闭）",
    "KALICO_PROGRESS_REPORT_MINUTES": "定时报告分钟数（0 关闭，5–1440）",
    "KALICO_WATCH_NOZZLE_FORCE": "喷嘴力监控（0 关闭，1 开启）",
    "HERMES_AGENT_WEBHOOK_URL": "Hermes Agent Webhook 地址（可选）",
    "KALICO_EVENT_MODES": "事件策略（例如 klippy_shutdown=both,nozzle_force_anomaly=both）",
    "KALICO_STATUS_FIELDS": "状态显示字段（逗号分隔 state,file,progress,nozzle,bed,chamber,elapsed,remaining）",
    "KALICO_CHAMBER_OBJECT": "腔体温度对象（留空自动发现；如 temperature_sensor chamber）",
}
SAFE = re.compile(r"^[A-Za-z0-9_./:@%+,~=-]+$")
CHAT_ID = re.compile(r"^[A-Za-z0-9_:@.\-]+$")


def default_path() -> Path:
    return Path(os.getenv(
        "KALICO_MONITOR_ENV_FILE", "~/.config/kalico-monitor/monitor.env"
    )).expanduser()


def defaults() -> dict[str, str]:
    return {
        "KALICO_DIRECT_CHAT_ID": "",
        "KALICO_AGENT_CHAT_ID": "",
        "KALICO_PRINTER_NAME": "PEEK-01",
        "MOONRAKER_URL": "http://127.0.0.1:7125",
        "MOONRAKER_API_KEY": "",
        "HERMES_WEBHOOK_URL": "http://127.0.0.1:8644/webhooks/kalico-alert",
        "HERMES_WEBHOOK_SECRET": "",
        "KALICO_MONITOR_DB": str(Path.home() / ".local/state/kalico-monitor/alerts.sqlite3"),
        "KALICO_ALLOWED_CHAT_IDS": "",
        "KALICO_DISCONNECT_SECONDS": "25",
        "KALICO_STALL_MINUTES": "0",
        "KALICO_PROGRESS_REPORT_MINUTES": "0",
        "KALICO_WATCH_NOZZLE_FORCE": "0",
        "HERMES_AGENT_WEBHOOK_URL": "",
        "KALICO_EVENT_MODES": "",
        "KALICO_STATUS_FIELDS": DEFAULT_CONFIG,
        "KALICO_CHAMBER_OBJECT": "",
    }


def read_env(path: Path) -> dict[str, str]:
    """Read env as data, never execute it."""
    values = defaults()
    if not path.is_file():
        from .monitor_cfg import cfg_path, read_cfg
        values.update(read_cfg(cfg_path(path)))
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$", line)
        if match and match.group(1) in values:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[match.group(1)] = value
    from .monitor_cfg import cfg_path, read_cfg
    values.update(read_cfg(cfg_path(path)))
    return values


def validate_field(key: str, value: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("不能包含换行或控制字符")
    if any(c in value for c in ('"', "'", "\\", "$", chr(96))):
        raise ValueError("不能包含引号、反斜杠或命令替换符号")
    value = value.strip()
    if key in {"MOONRAKER_URL", "HERMES_WEBHOOK_URL", "HERMES_AGENT_WEBHOOK_URL"}:
        if key == "HERMES_AGENT_WEBHOOK_URL" and not value:
            return ""
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or any(c.isspace() for c in value)):
            raise ValueError("请输入不带账号或查询参数的 HTTP(S) 地址")
        try:
            _ = url.port
        except ValueError as exc:
            raise ValueError("端口号无效") from exc
        if key == "MOONRAKER_URL" and url.path not in {"", "/"}:
            raise ValueError("Moonraker 地址不能包含路径")
        if (key in {"HERMES_WEBHOOK_URL", "HERMES_AGENT_WEBHOOK_URL"} and url.scheme == "http"
                and url.hostname not in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("非本机 Hermes Webhook 必须使用 HTTPS")
        return value.rstrip("/") if key == "MOONRAKER_URL" else value
    if key == "HERMES_WEBHOOK_SECRET":
        if not value or value in PLACEHOLDERS or len(value) < 16:
            raise ValueError("请输入至少 16 位真实密钥，或选择自动生成")
    elif key == "KALICO_EVENT_MODES":
        parse_modes(value)
        value = ",".join(x.strip() for x in value.split(",") if x.strip())
    elif key == "KALICO_STATUS_FIELDS":
        value = ",".join(parse_fields(value))
    elif key == "KALICO_CHAMBER_OBJECT":
        # Valid Moonraker thermal-object names: preserve internal spaces.
        if value and not re.fullmatch(
            r"(?:temperature_sensor|heater_generic|temperature_fan) [A-Za-z0-9_\-]{1,64}",
            value,
        ):
            raise ValueError("腔体对象应为 temperature_sensor/heater_generic/temperature_fan 加传感器名称")
    elif key == "KALICO_PRINTER_NAME":
        if not value:
            raise ValueError("打印机名称不能为空")
    elif key == "KALICO_MONITOR_DB":
        if not value:
            raise ValueError("数据库路径不能为空")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("数据库路径必须为绝对路径")
        value = str(path)
    elif key in {"KALICO_DIRECT_CHAT_ID", "KALICO_AGENT_CHAT_ID"}:
        if value and not re.fullmatch(r"oc_[A-Za-z0-9_-]+", value):
            raise ValueError("飞书会话 ID 必须为 oc_ 开头")
    elif key == "KALICO_ALLOWED_CHAT_IDS":
        ids = [x.strip() for x in value.split(",") if x.strip()]
        if any(not CHAT_ID.fullmatch(x) for x in ids):
            raise ValueError("会话 ID 含不支持的字符")
        value = ",".join(dict.fromkeys(ids))
    elif key in {"KALICO_DISCONNECT_SECONDS", "KALICO_STALL_MINUTES",
                 "KALICO_PROGRESS_REPORT_MINUTES"}:
        if not value.isdecimal():
            raise ValueError("请输入非负整数")
        number = int(value)
        if key == "KALICO_DISCONNECT_SECONDS" and number < 5:
            raise ValueError("断线确认时间至少为 5 秒")
        if key == "KALICO_PROGRESS_REPORT_MINUTES" and not (
            number == 0 or 5 <= number <= 1440
        ):
            raise ValueError("定时报告为 0（关闭）或 5–1440 分钟")
        value = str(number)
    elif key == "KALICO_WATCH_NOZZLE_FORCE":
        if value.lower() not in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
            raise ValueError("请输入 0 或 1")
        value = "1" if value.lower() in {"1", "true", "yes", "on"} else "0"
    return value


def validate_all(values: dict[str, str]) -> dict[str, str]:
    return {key: validate_field(key, values[key]) for key in KEYS}


def save_env(path: Path, values: dict[str, str]) -> None:
    """Atomic 0600 write, preserving comments and unmanaged keys."""
    values = validate_all(values)
    old = path.read_text(encoding="utf-8").splitlines() if path.is_file() else [
        "# Generated by kalico-monitor config; keep secrets out of Git."
    ]
    output, seen = [], set()
    for line in old:
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in values:
            if key not in seen:
                val = values[key]
                output.append(f'{key}={val if not val or SAFE.fullmatch(val) else chr(34) + val + chr(34)}')
                seen.add(key)
        else:
            output.append(line)
    for key in KEYS:
        if key not in seen:
            val = values[key]
            output.append(f'{key}={val if not val or SAFE.fullmatch(val) else chr(34) + val + chr(34)}')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".monitor-env-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output).rstrip("\n") + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_local_config(path: Path, values: dict[str, str]) -> None:
    """Safely write the ENV and CFG pair, rolling both back on errors.

    Values are prevalidated and prior file bytes captured before any change.
    This is failure-atomic for ordinary exceptions; a power cut between two
    replacements still requires recovery from the retained backups.
    """
    from .monitor_cfg import cfg_path, render_cfg, save_cfg

    values = validate_all(values)
    cfg = cfg_path(path)
    if path == cfg:
        raise ValueError("monitor.env 和 monitor.cfg 不能是同一文件")
    render_cfg(values)  # Validate the second file before modifying either.
    targets = (cfg, path)
    for target in targets:
        if target.is_symlink():
            raise ValueError(f"拒绝覆盖符号链接形式的配置文件：{target}")
    snapshots = {target: target.read_bytes() if target.exists() else None
                 for target in targets}
    touched: list[Path] = []
    try:
        save_cfg(cfg, values)
        touched.append(cfg)
        # Mark the env before writing: a monkeypatched/partial failure must
        # restore its bytes as well as the already-written cfg file.
        touched.append(path)
        save_env(path, values)
    except Exception:
        for target in reversed(touched):
            original = snapshots[target]
            if original is None:
                target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temp = tempfile.mkstemp(prefix=".monitor-restore-", dir=target.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        raise


def masked(key: str, value: str) -> str:
    if key in SECRET_KEYS:
        return "********（已设置）" if value and value not in PLACEHOLDERS else "（未设置）"
    return value or "（未设置）"


def show(values: dict[str, str], out=print) -> None:
    for key in KEYS:
        out(f"  {LABELS[key]}: {masked(key, values[key])}")


def test_moonraker(values: dict[str, str], out=print, timeout: int = 4) -> bool:
    """Read-only HTTP /server/info connectivity test."""
    try:
        url = validate_field("MOONRAKER_URL", values["MOONRAKER_URL"])
        headers = ({"X-Api-Key": values["MOONRAKER_API_KEY"]}
                   if values["MOONRAKER_API_KEY"] else {})
        request = urllib.request.Request(url + "/server/info", headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict) or "error" in result or "result" not in result:
            out("Moonraker 响应格式异常。")
            return False
    except (OSError, ValueError):
        out("Moonraker 连接失败：检查地址、网络、防火墙与 API Key。")
        return False
    out("Moonraker HTTP 连接成功；尚未验证 WebSocket 与飞书。")
    return True


def prompt_value(key: str, current: str, ask, secret_ask, out) -> tuple[str, bool]:
    if key == "KALICO_EVENT_MODES":
        from .monitor_cfg import edit_events
        return edit_events(current, ask, out), False
    if key in SECRET_KEYS:
        out(f"{LABELS[key]}: {masked(key, current)}")
        while True:
            if key == "HERMES_WEBHOOK_SECRET":
                choice = ask("  [1] 保留  [2] 自动生成  [3] 手动输入：").strip() or "1"
                if choice == "2":
                    return secrets.token_hex(32), True
                if choice == "1":
                    return current, False
                if choice != "3":
                    out("无效选项")
                    continue
            else:
                choice = ask("  [1] 保留  [2] 手动输入  [3] 清除：").strip() or "1"
                if choice == "1":
                    return current, False
                if choice == "3":
                    return "", False
                if choice != "2":
                    out("无效选项")
                    continue
            try:
                return validate_field(key, secret_ask("  新密钥（隐藏输入）：")), False
            except ValueError as exc:
                out(f"无效配置：{exc}")
    while True:
        raw = ask(f"{LABELS[key]} [{masked(key, current)}]（回车保留，- 清空）：").strip()
        if not raw:
            return current, False
        try:
            return validate_field(key, "" if raw == "-" else raw), False
        except ValueError as exc:
            out(f"无效配置：{exc}")


def wizard(path: Path, ask=input, secret_ask=getpass.getpass, out=print) -> bool:
    values = read_env(path)
    generated_secret = None
    out(f"Kalico Monitor 交互配置\n文件：{path}")
    while True:
        out("\n[1] Moonraker    [2] Hermes Webhook")
        out("[3] 数据与授权    [4] 监控与通知")
        out("[8] LLM 分析与事件策略  [9] /kalico status 显示字段")
        out("[5] 查看摘要      [6] 测试 Moonraker")
        out("[7] 保存退出      [0] 放弃修改")
        choice = ask("请选择：").strip()
        if choice in GROUPS:
            title, keys = GROUPS[choice]
            out(f"--- {title} ---")
            for key in keys:
                value, generated = prompt_value(key, values[key], ask, secret_ask, out)
                values[key] = value
                if key == "HERMES_WEBHOOK_SECRET":
                    generated_secret = value if generated else None
        elif choice == "5":
            show(values, out)
        elif choice == "6":
            test_moonraker(values, out)
        elif choice == "7":
            try:
                values = validate_all(values)
            except ValueError as exc:
                out(f"配置校验失败：{exc}。请返回菜单修改。")
                continue
            out("保存前摘要（密钥隐藏）：")
            show(values, out)
            if ask("确认写入？[y/N]：").strip().lower() not in {"y", "yes"}:
                out("未写入，返回菜单。")
                continue
            if any(mode == "agent" or mode == "both" for kind, mode in parse_modes(values["KALICO_EVENT_MODES"]).items()) and not values["HERMES_AGENT_WEBHOOK_URL"]:
                out("启用 agent/both 时必须配置 Hermes Agent Webhook 地址。")
                continue
            save_local_config(path, values)
            out(f"已保存：{path} 和对应 monitor.cfg（权限 0600）")
            if generated_secret and values["HERMES_WEBHOOK_SECRET"] == generated_secret:
                out("请将以下新 Secret 同步到 Hermes Webhook 路由（仅显示本次）：")
                out(generated_secret)
                out("请勿发送到群聊或提交 Git。")
            out("向导不会修改 ~/.hermes/config.yaml，也不会重启服务。")
            out("请同步 Hermes 路由 Secret 和目标 chat_id，然后执行：")
            out("  systemctl --user restart kalico-monitor")
            out("Hermes Gateway 如有旧环境变量覆盖，也需单独更新/重启。")
            return True
        elif choice == "0":
            out("已放弃，未修改配置文件。")
            return False
        else:
            out("无效选项。")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Kalico Monitor 本地交互配置")
    parser.add_argument("--file", type=Path, default=default_path())
    parser.add_argument("--apply", action="store_true", help="同步 Hermes 并重启 Monitor")
    parser.add_argument("--check", action="store_true", help="校验有效配置")
    parser.add_argument("--show", action="store_true", help="仅显示脱敏摘要")
    parser.add_argument("--test", action="store_true", help="仅测试 Moonraker HTTP")
    args = parser.parse_args(argv)
    path = args.file.expanduser()
    if args.apply:
        if not sys.stdin.isatty():
            parser.error("--apply requires interactive confirmation")
        values = validate_all(read_env(path))
        if input("同步 Hermes 路由并重启服务？[y/N]：").strip().lower() not in {"y", "yes"}:
            return
        from .bootstrap import configure
        home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
        warnings: list[str] = []
        configure(Path(__file__).resolve().parents[1], home, values["MOONRAKER_URL"],
                  values["KALICO_PRINTER_NAME"], "direct", "", path.resolve(), home / "config.yaml",
                  overrides=values, install_plugin=False, verification_warnings=warnings)
        for warning in warnings:
            print(warning)
        raise SystemExit(2 if warnings else 0)
    if args.check:
        values = validate_all(read_env(path))
        if any(v in {"agent", "both"} for v in parse_modes(values["KALICO_EVENT_MODES"]).values()) and not values["HERMES_AGENT_WEBHOOK_URL"]:
            parser.error("agent/both requires Hermes Agent Webhook")
        print("配置校验通过")
        return
    if args.show:
        show(read_env(path))
        return
    if args.test:
        raise SystemExit(0 if test_moonraker(read_env(path)) else 1)
    if not sys.stdin.isatty():
        parser.error("交互配置需要终端 TTY；非交互模式可用 --show 或 --test")
    try:
        wizard(path)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消，未保存配置。")


if __name__ == "__main__":
    main()
