# 配置参考

以下参数以当前 `kalico_monitor/config.py`、`quick.py` 和 `deploy/kalico-monitor.env.example` 为准。配置主要通过 `~/.config/kalico-monitor/monitor.env` 注入 systemd 服务；修改后重启生效。**目前没有通用的 `events.yaml` 规则引擎，也不支持在飞书中动态调整任意事件阈值。**

## 0. 交互式配置

~~~bash
.venv/bin/kalico-monitor config
.venv/bin/kalico-monitor config --show
.venv/bin/kalico-monitor config --test
# 自定义配置文件：
.venv/bin/kalico-monitor config --file /absolute/path/monitor.env
~~~

交互菜单包含打印机、Webhook、数据库与权限、监控通知以及 **[8] LLM 分析与事件策略**。按回车保留原值；交互菜单按功能分组，按回车保留原值，敏感字段使用隐藏输入；Webhook Secret 可以自动生成 64 位十六进制随机密钥。保存前进行 URL、路径、时间范围等校验，原子写回 `monitor.env` 并设置权限 `0600`；保留文件中的注释和非向导管理的其他变量。修改前可通过菜单取消，不会写入。当前向导只负责本地配置，**不会自动修改 Hermes 的 Webhook 路由 Secret、飞书目标聊天 ID，也不会自动重启 systemd 服务**。

该命令要求在已经安装本项目的 Python 虚拟环境中运行。监控服务原有无参数启动方式保持不变。

## 1. Monitor 常驻服务参数

| 参数 | 默认值 | 含义及约束 |
| --- | --- | --- |
| `KALICO_PRINTER_NAME` | `PEEK-01` | 消息中显示的打印机名称；当前一个实例对应一台打印机 |
| `MOONRAKER_URL` | `http://127.0.0.1:7125` | Moonraker HTTP(S) 根地址；分机部署时改为打印机局域网 IP |
| `MOONRAKER_API_KEY` | 空 | Moonraker 启用 API Key 时填写；不要提交 Git |
| `HERMES_WEBHOOK_URL` | `http://127.0.0.1:8644/webhooks/kalico-alert` | Hermes 签名 Webhook；**非 localhost HTTP 会被配置校验拒绝**，远程需 HTTPS |
| `HERMES_AGENT_WEBHOOK_URL` | 空（默认不用 LLM） | Agent 专用 Webhook；启用 `agent`/`both` 时必须配置；远程地址必须使用 HTTPS |
| `KALICO_EVENT_MODES` | 空（全部 `direct`） | 逗号分隔的 `kind=direct|agent|both`；支持 `default=...` |
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
| `KALICO_ALLOWED_CHAT_IDS` | 允许远程执行 `/kalico` 的聊天 ID，多个以英文逗号分隔；可留空并继承 Hermes Gateway 的入站授权，非空时是额外限制 |
| `MOONRAKER_URL`、`MOONRAKER_API_KEY` | 插件的实时只读 HTTP 查询 |
| `KALICO_MONITOR_DB` | `/kalico alerts`、`/kalico health` 读取的数据库路径 |
| `KALICO_PRINTER_NAME` | 快捷命令的打印机显示名 |

插件读取配置的优先级：Hermes Secret（有值时）→ Hermes 进程环境变量 → 共享配置文件。**Monitor 进程**只读取自身环境变量，不会解析 `KALICO_MONITOR_ENV_FILE`。若 Hermes 与 Monitor 分开运行，应保证两侧配置一致，数据库访问权限正确；不同机器上不能直接用另一台机器的本地 SQLite 路径。

用户准入、群聊及斜杠命令授权由 Hermes Gateway 负责；`KALICO_ALLOWED_CHAT_IDS` 仅在非空时进一步限制 chat_id。只有可信的 Hermes Gateway `platform/source.chat_id` 元数据可用于授权。**普通聊天消息文本中的 ID 不可作为身份依据**。本项目的斜杠命令全部只读；插件工具本身也只读，但不应因此把 Moonraker 的高权限 API 暴露到公网。

## 3. 示例：打印机和 AI 主机分离

假设打印机 Moonraker 为 `192.168.1.100:7125`，AI 主机为 `192.168.1.101`，且 Monitor 与 Hermes 同机：

~~~ini
KALICO_PRINTER_NAME=PEEK-01
MOONRAKER_URL=http://192.168.1.100:7125
MOONRAKER_API_KEY=
HERMES_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-alert
HERMES_WEBHOOK_SECRET=替换为真实随机密钥
KALICO_MONITOR_DB=/home/YOUR_USER/.local/state/kalico-monitor/alerts.sqlite3
KALICO_ALLOWED_CHAT_IDS=
HERMES_AGENT_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-analysis
KALICO_EVENT_MODES=klippy_shutdown=both,nozzle_force_anomaly=both
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

**已经实现**：断线确认时间、停滞检测开关/窗口、定时报告开关/间隔、喷嘴力桥接开关、可选聊天限制、打印机名称、网络与密钥，以及逐事件 `direct` / `agent` / `both` 路由。

**尚未实现**：任意事件的独立 `enabled` 开关、每种事件的严重程度自定义、每事件独立冷却时间、通用 YAML 条件规则、按打印机动态切换、首层 Z 偏移独立通知、飞书远程修改规则。这些属于后续规则引擎/事件桥接扩展，不能直接把示例 YAML 当成现有配置。

## 5. 运维注意

- `monitor.env` 包含 Webhook Secret 和可能的 Moonraker API Key，权限应为 `600`，不要粘贴到群聊或提交 Git。
- SQLite 中保留历史通知、打印文件名和错误上下文；应保护文件权限并考虑定期备份与清理。**当前没有自动清理过期历史消息的机制**。
- 定时报告由 Monitor 本地调度，不需要 Cron；不应再启用其他重复的轮询告警服务。
- Hermes 返回非成功状态或网络不可达时，消息会留在队列中重试；网络恢复可能补发历史报告。

## 6. 按事件选择 LLM 路由

`KALICO_EVENT_MODES` 并非 `events.yaml`，而是当前已支持的字符串策略。示例：

~~~ini
HERMES_AGENT_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/kalico-analysis
KALICO_EVENT_MODES=complete=direct,paused=direct,print_progress=direct,klippy_shutdown=both,nozzle_force_anomaly=both
~~~

- `direct`：仅调用 Hermes `kalico-alert` Webhook；此路由在 Hermes 侧应设置 `deliver_only: true`。
- `agent`：仅调用 `kalico-analysis`，由 Hermes Agent 受理任务。
- `both`：生成两条独立持久化投递任务，普通通知和 AI 受理互不阻塞；两路分别有幂等请求 ID、失败次数和重试时间。

`KALICO_EVENT_MODES` 留空时所有事件为 `direct`；添加 `default=agent` 或 `default=both` 会对**全部未单独覆盖的事件**启用模型路由，可能增加调用成本。Agent 响应 JSON `accepted` 仅代表已受理，不表示 LLM 分析完成或飞书实际送达。旧版数据库会自动增加 `alert_routes` 表；注意只在 Monitor 第一次处理事件时确定路由，修改配置不会 retroactively 改变已分配任务。详见 [事件路由说明](EVENT_ROUTING.zh-CN.md)。
