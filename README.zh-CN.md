# Hermes Kalico Monitor

**Kalico / Klipper 实时监控与 Hermes 消息通知插件**

[English](README.md) · [安装与部署](docs/INSTALL.zh-CN.md) · [配置参考](docs/CONFIGURATION.zh-CN.md) · [事件与命令](docs/EVENTS.zh-CN.md) · [故障排查](docs/TROUBLESHOOTING.zh-CN.md)

> **状态**：实验性项目。当前 `plugin.yaml` 标记 v0.3.0，`pyproject.toml` 仍标记 0.2.0，尚未完成统一正式发布和真实打印机—Hermes Gateway—飞书端到端验收。模拟测试不等于实机可靠性或安全认证。

本项目面向高温 PEEK 等 3D 打印设备，通过 Moonraker 实时订阅 Kalico 状态，自动识别打印完成、暂停、故障、连接异常和可选喷嘴力回弹事件，经 Hermes Webhook 向飞书等已连接渠道发送通知。常规告警不调用 LLM，也不依赖摄像头或 Obico。

## 架构

```text
打印机控制主机                         AI 服务主机（RK3588 / Jetson / PC）
Kalico + Moonraker --LAN HTTP/WS--> Kalico Monitor (systemd + SQLite)
                                        |
                                        v
                                  Hermes Gateway -> 飞书
                                  Hermes 插件 -----> Moonraker 只读查询
```

**可以同机，也可以局域网分机部署**。Monitor 与 Hermes Gateway 建议同机；打印机与 AI 主机只需网络互通，不必同网段。打印机本地独立负责加热、运动、故障保护及 PR #13 的可选暂停/首层 Z 修正；本项目**不发送任何 G-code 或打印机控制指令**。

## 功能

| 功能 | 状态 |
| --- | --- |
| Moonraker WebSocket 实时状态订阅 | 已实现 |
| 打印完成、暂停、取消、错误及 Klippy/Moonraker 故障告警 | 已实现 |
| SQLite 告警持久化、失败重试和去重 | 已实现 |
| 打印过程定时状态报告 | 可选，默认关闭 |
| 停滞检测 | 可选，默认关闭 |
| 自定义 `nozzle_force_monitor` 疑似堵头告警 | 可选，默认关闭；依赖 [Kalico PR #13](https://github.com/wangyh775/kalico/pull/13) |
| `/kalico status/temp/force/alerts/health` 零 LLM 查询 | 已实现，需聊天白名单 |
| 通用 `events.yaml` 规则引擎、多打印机聚合、AI 自动控制 | 尚未实现 |

## 快速开始（Linux）

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
bash scripts/setup-user-service.sh
.venv/bin/kalico-monitor config
```

填写 `MOONRAKER_URL`、`HERMES_WEBHOOK_SECRET`、`KALICO_MONITOR_DB`、`KALICO_ALLOWED_CHAT_IDS` 等。将 [Webhook 示例](docs/hermes-webhook.example.yaml) 合并进 `~/.hermes/config.yaml`（**不要覆盖原配置**），配置相同 Secret 和目标飞书群 ID。

```bash
hermes plugins install "file://$PWD" --enable
systemctl --user enable --now kalico-monitor
journalctl --user -u kalico-monitor -f
```

脚本只准备服务，不自动启动；Hermes 插件与 Monitor 是两个独立组件。以上仅是概览，**首次部署请按照 [完整安装指南](docs/INSTALL.zh-CN.md)** 操作，包括前台验证、权限及实机验收。

## 常用配置

```ini
KALICO_PRINTER_NAME=PEEK-01
MOONRAKER_URL=http://192.168.1.100:7125
HERMES_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-alert
HERMES_WEBHOOK_SECRET=替换为随机密钥
KALICO_DISCONNECT_SECONDS=25
KALICO_STALL_MINUTES=0
KALICO_PROGRESS_REPORT_MINUTES=30
KALICO_WATCH_NOZZLE_FORCE=0
```

`192.168.1.100` 是示例 IP。默认关闭定时报告和喷嘴力监控；上例主动将定时报告设为 30 分钟。详细默认值、参数限制和分机部署见 [配置参考](docs/CONFIGURATION.zh-CN.md)。

## 自定义喷嘴力模块兼容性

当前 Monitor 通过 `triggered` 和 `trigger_count` 识别 [Kalico PR #13](https://github.com/wangyh775/kalico/pull/13) 的疑似堵头暂停事件。**最新 PR #13 还支持首层 Z 偏移，但成功修正或仅记录回弹并不会自动产生独立飞书告警。** `/kalico force` 也尚未完整展示首层控制状态。详见 [事件与命令](docs/EVENTS.zh-CN.md)。

## 测试与安全

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

测试使用模拟 Moonraker 事件和本地 SQLite。当前未验证实际 Hermes Gateway 与飞书版本兼容性；Webhook 配置须在部署版本实测。高温设备必须保留独立过温保护、急停和断电措施，不能依赖 Hermes 代替安全联锁。

项目来源及许可说明见 [NOTICE.md](NOTICE.md)；开发约定见 [仓库指南](docs/REPOSITORY_SETUP.zh-CN.md)。
