"""Enable unattended boot of Kalico Monitor's user service (not Hermes).

Linger is a per-user systemd/logind property. It allows an enabled user
service to start when the machine boots, before that user logs in.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess

SERVICE = "kalico-monitor"


def _call(args: list[str], *, interactive: bool = False) -> subprocess.CompletedProcess:
    options = {} if interactive else {"capture_output": True, "text": True}
    return subprocess.run(args, check=False, timeout=120 if interactive else 15, **options)


def login_user() -> str:
    """Use the real invoking account, not an untrusted USER environment value."""
    import pwd

    return pwd.getpwuid(os.getuid()).pw_name


def _require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"缺少 {name}；需要 systemd-logind 环境。")


def service_ready() -> None:
    """Do not enable user-wide linger unless the Monitor unit is installed."""
    _require_command("systemctl")
    enabled = _call(["systemctl", "--user", "is-enabled", SERVICE])
    if enabled.returncode != 0 or enabled.stdout.strip() != "enabled":
        raise RuntimeError(
            "Kalico Monitor 用户服务尚未持久启用。请先运行 bash install.sh "
            "完成安装，或执行 systemctl --user enable --now kalico-monitor。"
        )
    active = _call(["systemctl", "--user", "is-active", SERVICE])
    if active.returncode != 0 or active.stdout.strip() != "active":
        raise RuntimeError(
            "Kalico Monitor 服务当前未运行。请先检查 "
            "systemctl --user status kalico-monitor。"
        )


def linger_enabled(username: str) -> bool:
    _require_command("loginctl")
    result = _call(["loginctl", "show-user", username, "-p", "Linger"])
    if result.returncode != 0:
        raise RuntimeError(
            "无法读取 Linger 状态；请检查 systemd-logind 是否运行，"
            "或手工执行 loginctl show-user 用户名 -p Linger。"
        )
    state = result.stdout.strip()
    if state == "Linger=yes":
        return True
    if state == "Linger=no":
        return False
    raise RuntimeError(f"无法识别的 Linger 状态：{state!r}")


def ensure_boot_autostart(*, check_only: bool = False) -> bool:
    """Return True only if this invocation enabled linger for the first time.

    The only privilege elevation is the explicit logind enable-linger action;
    this function never writes Hermes files or touches its service.
    """
    service_ready()
    username = login_user()
    if linger_enabled(username):
        return False
    if check_only:
        raise RuntimeError(
            f"用户 {username} 的 Linger 尚未启用。请运行 "
            "bash install.sh --enable-autostart。"
        )
    if os.geteuid() == 0:
        command = ["loginctl", "enable-linger", username]
    elif shutil.which("sudo"):
        command = ["sudo", "loginctl", "enable-linger", username]
    else:
        # On some machines polkit permits the user's own loginctl action.
        command = ["loginctl", "enable-linger", username]
    print(f"正在为 Linux 用户 {username} 启用 Linger（可能需要 sudo 授权）。")
    print("Linger 是用户级设置，可能同时影响该用户其他已启用的 systemd 服务。")
    result = _call(command, interactive=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"无法启用 Linger（退出码 {result.returncode}）。"
            f"请手工运行 sudo loginctl enable-linger {username} 后重试。"
        )
    if not linger_enabled(username):
        raise RuntimeError("loginctl 命令返回成功，但 Linger 状态仍不是 yes。")
    service_ready()
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Kalico Monitor 无人登录开机自启")
    parser.add_argument("--check", action="store_true", help="仅读取状态，不修改登录管理配置")
    args = parser.parse_args(argv)
    try:
        changed = ensure_boot_autostart(check_only=args.check)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(
            1,
            "Kalico Monitor 服务安装与开机自启检查是两个阶段；"
            f"目前无人登录开机自启尚未验证成功：{exc}\n"
        )
    print("Kalico Monitor：用户服务 enabled，当前 active，Linger=yes。")
    if changed:
        print("已启用无人登录开机自启；重启后无需登录即可由 systemd 自动启动。")
    else:
        print("无人登录开机自启已配置，无需重复授权。")


if __name__ == "__main__":
    main()
