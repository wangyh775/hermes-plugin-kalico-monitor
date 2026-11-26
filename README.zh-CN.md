# Hermes Kalico Monitor

**Kalico / Klipper 实时监控与 Hermes 消息通知插件**

[English](README.md) · [安装与部署](docs/INSTALL.zh-CN.md) · [配置参考](docs/CONFIGURATION.zh-CN.md) · [事件与命令](docs/EVENTS.zh-CN.md) · [事件路由](docs/EVENT_ROUTING.zh-CN.md) · [故障排查](docs/TROUBLESHOOTING.zh-CN.md)

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
| `/kalico status/temp/force/alerts/health` 零 LLM 查询 | 已实现；由 Hermes 授权，可选附加聊天白名单 |
| 按事件设置 `direct` / `agent` / `both` | 已实现，默认全部 `direct` |
| SQLite 双路投递、独立重试与幂等 ID | 已实现 |
| 通用 `events.yaml` 条件规则、多打印机聚合、AI 自动控制 | 尚未实现 |

## 自定义 /kalico status 显示内容

支持在 `bash install.sh --reconfigure` 的 **[12] 状态显示字段** 中按编号勾选内容，最终通过 **[11] 确认并保存**；**[13]** 可设置腔体温度传感器的 Moonraker 对象名。默认包含打印状态、文件名、打印进度、喷嘴温度、热床温度、腔体温度、已打印时间及预计剩余时间。

配置文件示例：

```ini
KALICO_STATUS_FIELDS=state,file,progress,nozzle,bed,chamber,elapsed,remaining
KALICO_CHAMBER_OBJECT=
```

腔体温度留空时按对象名称尝试自动发现，不支持时显示 `—`。剩余时间使用 `print_duration` 和 G-code 文件进度粗略估算，不代表切片器提供的预测；状态或数据不足时显示 `—`。详见 [配置参考](docs/CONFIGURATION.zh-CN.md)。

## Linux 一键安装（推荐）

如果 Hermes 已经完成飞书及模型配置，可使用一键安装入口：

```bash
bash install.sh
```

Linux 安装成功后，还会配置 **Kalico Monitor 无人登录开机自启**（systemd 用户服务 + Linger），首次可能请求 sudo 授权；不修改 Hermes 启动。之后可用 `bash install.sh --check-autostart` 检查，或 `bash install.sh --enable-autostart` 单独补配。

向导会配置 Moonraker、自动合并 Hermes Webhook、安装插件、管理 systemd，并使用安全的测试通知验证飞书通道。默认 `PEEK-01` 与关键故障 `both`；具体权限前提、备份、回滚和限制见 [Linux 一键安装](docs/ONE_CLICK_INSTALL.zh-CN.md)。

## 安装后完整自检

安装和二次配置结束后，`install.sh` 自动进行只读自检。可随时单独执行：

```bash
bash install.sh --check
```

分项报告包括 Monitor 配置和 SQLite 心跳、systemd/Linger、Hermes 插件安装/启用状态与只读命令注册代码、Webhook 配置与密钥、Moonraker HTTP/WebSocket 连接、Hermes Gateway 健康检查。**FAIL 代表明确失败，WARN 表示尚未证明通过**；失败不会自动暂停打印或禁用 Monitor。飞书实际命令加载和消息最终接收仍需在真实会话验收。

代码更新不止 `git pull`：本地源码、运行中的 Monitor 服务和 Hermes 安装副本是独立的。更新后需要按情况更新 Python 环境、重启 Monitor、同步 Hermes 插件并重载 Gateway，最后运行自检。详细步骤见 [安装与部署](docs/ONE_CLICK_INSTALL.zh-CN.md)。

## 已安装后的二次交互配置

无需重新安装 Hermes 插件，直接在仓库目录执行：

```bash
bash install.sh --reconfigure
```

向导读取当前 `monitor.env`，展示已有值并允许逐项修改 Moonraker 地址、打印机名称、API Key、喷嘴力开关、报告周期、停滞检测、断线阈值、事件路由及可选聊天限制。也可以只更改 `klippy_shutdown` / `nozzle_force_anomaly` 的 `direct/agent/both` 模式，其他事件规则会保留。按回车保留原值，选择取消不会修改文件；提交后自动备份、同步 Hermes 路由及重启服务，并重新测试连通性。修改飞书与大模型本身仍由 Hermes 负责。详见 [Linux 安装与重配置](docs/ONE_CLICK_INSTALL.zh-CN.md)。

## 手动安装（Linux）

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
bash scripts/setup-user-service.sh
.venv/bin/kalico-monitor config
```

填写 `MOONRAKER_URL`、`HERMES_WEBHOOK_SECRET`、`KALICO_MONITOR_DB` 等。`KALICO_ALLOWED_CHAT_IDS` 是可选的额外限制；入站权限由 Hermes 负责。需要 AI 分析时，在向导菜单 **8** 设置 Agent Webhook 地址与事件模式。将 [Webhook 示例](docs/hermes-webhook.example.yaml) 合并进 `~/.hermes/config.yaml`（**不要覆盖原配置**），配置相同 Secret 和目标飞书群 ID。

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
HERMES_AGENT_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-analysis
KALICO_EVENT_MODES=klippy_shutdown=both,nozzle_force_anomaly=both
```

`192.168.1.100` 是示例 IP。默认关闭定时报告、喷嘴力监控和 LLM 路由；上例主动开启 30 分钟报告及两种异常的 `both` 模式。**Agent 路由和地址必须同步在 Hermes 中配置，否则对应事件无法正常发送**。详细默认值、参数限制和分机部署见 [配置参考](docs/CONFIGURATION.zh-CN.md)。

## 自定义喷嘴力模块兼容性

当前 Monitor 通过 `triggered` 和 `trigger_count` 识别 [Kalico PR #13](https://github.com/wangyh775/kalico/pull/13) 的疑似堵头暂停事件。**最新 PR #13 还支持首层 Z 偏移，但成功修正或仅记录回弹并不会自动产生独立飞书告警。** `/kalico force` 也尚未完整展示首层控制状态。详见 [事件与命令](docs/EVENTS.zh-CN.md)。

## 测试与安全

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

测试使用模拟 Moonraker 事件和本地 SQLite。当前未验证实际 Hermes Gateway 与飞书版本兼容性；Webhook 配置须在部署版本实测。高温设备必须保留独立过温保护、急停和断电措施，不能依赖 Hermes 代替安全联锁。

项目来源及许可说明见 [NOTICE.md](NOTICE.md)；开发约定见 [仓库指南](docs/REPOSITORY_SETUP.zh-CN.md)。

## 升级与事件路由

原 PR #7（交互配置向导）与 PR #8（Hermes 权限及双路事件路由）均已合并。既有部署升级时运行 `git pull`，再执行 `.venv/bin/python -m pip install -e .` 和 `.venv/bin/kalico-monitor config` 检查新增字段。配置保存后重启 Monitor；如 Hermes 配置有变更，还需按 Hermes 部署方式重载 Gateway。默认所有事件均为 `direct`，不会因升级自动调用 LLM。

事件模式说明、幂等机制、旧 SQLite 兼容和风险边界见 [事件路由](docs/EVENT_ROUTING.zh-CN.md)。GitHub CI 已覆盖 Python 3.10、3.12 与 Ruff；**尚未完成真实飞书和 Hermes 端到端验收**。
