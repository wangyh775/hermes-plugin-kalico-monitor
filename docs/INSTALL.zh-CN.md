# 安装与升级

简体中文 | [English](INSTALL.md) · [首页](../README.zh-CN.md)

> 依据 main 提交 0c47b84（PR #21 已合并）。Monitor 只读，打印机—Hermes—飞书实机验收尚未完成。

## Linux 部署

要求可达的 Moonraker 主机及已配置 Hermes Gateway/飞书。Monitor 与 Hermes 建议同机，远程 Hermes Webhook 需 HTTPS。不要提交密钥。引导安装配置服务、插件与 Webhook 路由，备份及测试，保留已有 Hermes 配置。

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
bash install.sh
bash install.sh --check
```

手动部署：`bash scripts/setup-user-service.sh`、`.venv/bin/kalico-monitor config`，合并 Webhook 示例至 Hermes 配置，然后：

```bash
hermes plugins install "file://$PWD" --enable
systemctl --user enable --now kalico-monitor
journalctl --user -u kalico-monitor -f
```

setup 脚本只准备服务，不主动启动。Linger 实现用户服务无需登录自启；`bash install.sh --check-autostart` 检查。升级：备份 env/CFG/DB/Hermes 配置，git pull --ff-only，`.venv/bin/python -m pip install -e .` 更新 Python 环境，重新配置、重启 Monitor，必要时同步 Hermes 插件并重载 Gateway。仅改配置用 `bash install.sh --reconfigure`。必须自检并实际群验证。详见 [原一键安装指南](ONE_CLICK_INSTALL.zh-CN.md) 的备份与回滚。
