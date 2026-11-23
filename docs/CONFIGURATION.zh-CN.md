# 配置参考

以下参数以当前 `kalico_monitor/config.py`、`quick.py` 和 `deploy/kalico-monitor.env.example` 为准。配置主要通过 `~/.config/kalico-monitor/monitor.env` 注入 systemd 服务；修改后重启生效。**目前没有通用的 `events.yaml` 规则引擎，也不支持在飞书中动态调整任意事件阈值。**

## 0. 交互式配置（新增）

~~~bash
.venv/bin/kalico-monitor config
.venv/bin/kalico-monitor config --show
.venv/bin/kalico-monitor config --test
# 自定义配置文件：
.venv/bin/kalico-monitor config --file /absolute/path/monitor.env
~~~

交互菜单按功能分组，按回车保留原值，敏感字段使用隐藏输入；Webhook Secret 可以自动生成 64 位十六进制随机密钥。保存前进行 URL、路径、时间范围等校验，原子写回 `monitor.env` 并设置权限 `0600`；保留文件中的注释和非向导管理的其他变量。修改前可通过菜单取消，不会写入。当前向导只负责本地配置，**不会自动修改 Hermes 的 Webhook 路由 Secret、飞书目标聊天 ID，也不会自动重启 systemd 服务**。

该命令要求在已经安装本项目的 Python 虚拟环境中运行。监控服务原有无参数启动方式保持不变。

## 1. Monitor 常驻服务参数

| 参数 | 默认值 | 含义及约束 |
| --- | --- | --- |
| `KALICO_PRINTER_NAME` | `PEEK-01` | 消息中显示的打印机名称；当前一个实例对应一台打印机 |
| `MOONRAKER_URL` | `http://127.0.0.1:7125` | Moonraker HTTP(S) 根地址；分机部署时改为打印机局域网 IP |
| `MOONRAKER_API_KEY` | 空 | Moonraker 启用 API Key 时填写；不要提交 Git |
| `HERMES_WEBHOOK_URL` | `http://127.0.0.1:8644/webhooks/kalico-alert` | Hermes 签名 Webhook；**非 localhost HTTP 会被配置校验拒绝**，远程需 HTTPS |
| `HERMES_WEBHOOK_SECRET` | 无（必填） | Webhook HMAC 密钥；必须与 Hermes 路由一致；`INSECURE_NO_AUTH` 被拒绝 |
| `KALICO_MONITOR_DB` | `~/.local/state/kalico-monitor/alerts.sqlite3` | SQLite 状态、告警与重试队列；一个实例独占自己的路径 |
| `KALICO_DISCONNECT_SECONDS` | `25` | Klippy/Moonraker 断连确认窗口，最小 5 秒 |
| `KALICO_STALL_MINUTES` | `0` | 打印停滞检测窗口，0 为关闭；仅根据状态/进度判断，可能误报 |
| `KALICO_PROGRESS_REPORT_MINUTES` | `0` | 打印中定时报告，0 为关闭；启用时允许 5–1440 分钟 |
| `KALICO_WATCH_NOZZLE_FORCE` | `0` | 订阅自定义 `nozzle_force_monitor`；支持 0/1、true/false、yes/no、on/off |

`MOONRAKER_URL` 会转换为对应 `ws://` 或 `wss://` 的 `/websocket` 订阅地址。监控器连接 Moonraker 时使用 `X-Api-Key`（若配置），不会自动发现打印机地址。

## 2. Hermes 插件参数与权限

| 参数 | 用途 |
| --- | --- |
| `KALICO_MONITOR_ENV_FILE` | 插件读取共享配置文件的路径，默认 `~/.config/kalico-monitor/monitor.env`；路径按 Hermes 进程用户的 HOME 解析 |
| `KALICO_ALLOWED_CHAT_IDS` | 允许远程执行 `/kalico` 的聊天 ID，多个以英文逗号分隔；未配置则拒绝远程命令 |
| `MOONRAKER_URL`、`MOONRAKER_API_KEY` | 插件的实时只读 HTTP 查询 |
| `KALICO_MONITOR_DB` | `/kalico alerts`、`/kalico health` 读取的数据库路径 |
| `KALICO_PRINTER_NAME` | 快捷命令的打印机显示名 |

插件读取配置的优先级：Hermes Secret（有值时）→ Hermes 进程环境变量 → 共享配置文件。**Monitor 进程**只读取自身环境变量，不会解析 `KALICO_MONITOR_ENV_FILE`。若 Hermes 与 Monitor 分开运行，应保证两侧配置一致，数据库访问权限正确；不同机器上不能直接用另一台机器的本地 SQLite 路径。

只有可信的 Hermes Gateway `platform/source.chat_id` 元数据可用于授权。**普通聊天消息文本中的 ID 不可作为身份依据**。本项目的斜杠命令全部只读；插件工具本身也只读，但不应因此把 Moonraker 的高权限 API 暴露到公网。

## 3. 示例：打印机和 AI 主机分离

假设打印机 Moonraker 为 `192.168.1.100:7125`，AI 主机为 `192.168.1.101`，且 Monitor 与 Hermes 同机：

~~~ini
KALICO_PRINTER_NAME=PEEK-01
MOONRAKER_URL=http://192.168.1.100:7125
MOONRAKER_API_KEY=
HERMES_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-alert
HERMES_WEBHOOK_SECRET=替换为真实随机密钥
KALICO_MONITOR_DB=/home/YOUR_USER/.local/state/kalico-monitor/alerts.sqlite3
KALICO_ALLOWED_CHAT_IDS=oc_你的飞书群ID
KALICO_DISCONNECT_SECONDS=25
KALICO_STALL_MINUTES=0
KALICO_PROGRESS_REPORT_MINUTES=30
KALICO_WATCH_NOZZLE_FORCE=1
~~~

`KALICO_WATCH_NOZZLE_FORCE=1` 仅在打印机已安装、启用并验证 PR #13 的自定义模块后设置。修改完毕：

~~~bash
chmod 600 ~/.config/kalico-monitor/monitor.env
systemctl --user restart kalico-monitor
journalctl --user -u kalico-monitor -n 50 --no-pager
~~~

若 Hermes Gateway 需要重新读取自己的插件环境，也要按 Hermes 部署方式重新加载或重启 Gateway；只重启 Monitor 不会改变 Hermes 进程的环境变量。

## 4. 当前可以配置什么，不能配置什么？

**已经实现**：断线确认时间、停滞检测开关/窗口、定时报告开关/间隔、喷嘴力桥接开关、聊天白名单、打印机名称、网络与密钥。

**尚未实现**：任意事件的独立 `enabled` 开关、每种事件的严重程度自定义、每事件独立路由/冷却时间、通用 YAML 条件规则、按打印机动态切换、首层 Z 偏移独立通知、飞书远程修改规则。这些属于后续规则引擎/事件桥接扩展，不能直接把示例 YAML 当成现有配置。

## 5. 运维注意

- `monitor.env` 包含 Webhook Secret 和可能的 Moonraker API Key，权限应为 `600`，不要粘贴到群聊或提交 Git。
- SQLite 中保留历史通知、打印文件名和错误上下文；应保护文件权限并考虑定期备份与清理。**当前没有自动清理过期历史消息的机制**。
- 定时报告由 Monitor 本地调度，不需要 Cron；不应再启用其他重复的轮询告警服务。
- Hermes 返回非成功状态或网络不可达时，消息会留在队列中重试；网络恢复可能补发历史报告。
