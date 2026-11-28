# Installation and upgrade

English | [简体中文](INSTALL.zh-CN.md) · [Home](../README.md)

> Reference: main commit 0c47b84 (PR #21 merged). Read-only monitor; live printer–Hermes–Feishu acceptance outstanding.

## Linux deployment

Use a reachable Moonraker host and configured Hermes Gateway/Feishu. Prefer same host for Hermes and Monitor; remote Hermes webhooks require HTTPS. Never commit credentials. The bootstrap configures the service, plugin and webhook routes, backs up config and tests; use the existing Hermes configuration instead of replacing it.

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
bash install.sh
bash install.sh --check
```

Manual deployment: `bash scripts/setup-user-service.sh`, `.venv/bin/kalico-monitor config`, merge webhook example into Hermes config, then:

```bash
hermes plugins install "file://$PWD" --enable
systemctl --user enable --now kalico-monitor
journalctl --user -u kalico-monitor -f
```

Setup script prepares service, not automatic start. Linger enables login-free boot for user service; check `bash install.sh --check-autostart`. Upgrade: back up env/CFG/DB/Hermes config, git pull --ff-only, reinstall Python package `.venv/bin/python -m pip install -e .`, reconfigure, restart Monitor, update installed Hermes plugin and reload Gateway as required. Use `bash install.sh --reconfigure` for configuration only. Check and actual chat test are both needed. See [one-click legacy guide](ONE_CLICK_INSTALL.zh-CN.md) for detailed backups and rollback.
