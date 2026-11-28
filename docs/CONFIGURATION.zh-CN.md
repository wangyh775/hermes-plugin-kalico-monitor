# 配置参考

简体中文 | [English](CONFIGURATION.md) · [首页](../README.zh-CN.md)

> 依据 main 提交 0c47b84（PR #21 已合并）。Monitor 只读，打印机—Hermes—飞书实机验收尚未完成。

`monitor.cfg` 是独立 INI，通常位于 ~/.config/kalico-monitor/monitor.cfg，与 monitor.env 同目录；覆盖对应 ENV 业务项。密钥/API Key 与 Webhook URL 留在 ENV。允许节 monitor、printer、status、hermes、events；未知节/参数报错，不执行 G-code 或任意表达式。示例（合入现有文件）：

```ini
[monitor]
progress_report_minutes: 0
stall_minutes: 0
disconnect_seconds: 25
watch_nozzle_force: 1
[printer]
name: PEEK-01
moonraker_url: http://192.168.1.100:7125
[status]
fields: state,file,progress,nozzle,bed,chamber,elapsed,remaining
chamber_object:
[events]
default: direct
filament_jam_confirmed: both
```

示例 IP。通过向导配置 [hermes] direct_chat_id、agent_chat_id；空值继承默认群。菜单 15/16 输入 `-` 清除显式群。both 前必须配置 Agent。状态默认字段如上；预计剩余为 duration×(1−progress)/progress，不是切片器估计。

| ENV | 默认 / 约束 |
|---|---|
| `KALICO_PRINTER_NAME` | PEEK-01 |
| `MOONRAKER_URL` | http://127.0.0.1:7125 |
| `MOONRAKER_API_KEY` | 未要求时留空 |
| `HERMES_WEBHOOK_URL` | http://127.0.0.1:8644/webhooks/kalico-alert |
| `HERMES_AGENT_WEBHOOK_URL` | 空；非本机 HTTPS |
| `HERMES_WEBHOOK_SECRET` | 必填随机密钥 |
| `KALICO_EVENT_MODES` | default=direct; off/direct/agent/both |
| `KALICO_MONITOR_DB` | ~/.local/state/kalico-monitor/alerts.sqlite3 |
| `KALICO_DISCONNECT_SECONDS` | 25; ≥5 s |
| `KALICO_STALL_MINUTES` | 0 (off) |
| `KALICO_PROGRESS_REPORT_MINUTES` | 0 (off); 5–1440 min |
| `KALICO_WATCH_NOZZLE_FORCE` | 0 (off); legacy/new optional objects |
| `KALICO_ALLOWED_CHAT_IDS` | 可选额外入站限制 |
| `KALICO_STATUS_FIELDS` | state,file,progress,nozzle,bed,chamber,elapsed,remaining |
| `KALICO_CHAMBER_OBJECT` | 空；自动发现 |
| `KALICO_MONITOR_ENV_FILE` | ~/.config/kalico-monitor/monitor.env |
| `KALICO_MONITOR_CFG` | 可选显式路径 |

config --check 校验；config --apply 确认后同步集成及重启。编辑前备份。不要把 HMAC 密钥放入截图/日志。

---

## 既有部署细节与历史说明（保留）

以下为原有详细说明；若旧版本描述与前面当前源码基准冲突，以前面为准。

# 配置参考

以下参数以当前 `kalico_monitor/config.py`、`quick.py` 和 `deploy/kalico-monitor.env.example` 为准。配置主要通过 `~/.config/kalico-monitor/monitor.env` 注入 systemd 服务；修改后重启生效。**目前没有通用的 `events.yaml` 规则引擎，也不支持在飞书中动态调整任意事件阈值。**


## Monitor CFG 配置与事件管理

> PR #19 已合并；PR #20 修复配置与通知策略一致性问题。仍需在部署环境验证 Hermes Gateway、飞书群与真实打印事件，不建议在无人值守生产打印时将通知作为唯一故障保护。

配置目录为 `~/.config/kalico-monitor/`。`monitor.cfg` 是独立 Monitor 服务的配置，不是 Kalico 的 printer.cfg。CFG 中明确设置的业务字段优先于旧 ENV；未设置的字段继续使用 ENV。当前安装器仍会同步部分 ENV 字段以兼容旧版本，尚未完全消除重复存储。API Key、Webhook Secret 保留在 ENV，勿提交到 Git。

```ini
[printer]
name: PEEK-01
moonraker_url: http://192.168.31.10:7125

[monitor]
disconnect_seconds: 25
stall_minutes: 5
progress_report_minutes: 30
watch_nozzle_force: false

[hermes]
direct_chat_id: oc_xxxxxxxxx
agent_chat_id: oc_yyyyyyyyy

[status]
fields: state,file,progress,nozzle,bed,chamber,elapsed,remaining
chamber_object: temperature_sensor chamber

[events]
default: direct
started: direct
paused: direct
resumed: direct
complete: direct
cancelled: direct
error: both
stalled: both
print_progress: direct
klippy_shutdown: both
klippy_disconnected: direct
klippy_recovered: direct
moonraker_unreachable: direct
moonraker_recovered: direct
nozzle_force_anomaly: both
```

事件策略：`off` 禁止新通知；`direct` 零 LLM 直推；`agent` 模型分析；`both` 两路独立投递。PR #20 起，Monitor 重启加载新策略时，还会撤销旧队列中不再允许的待发送路由；已经实际投递的路由保留历史记录。`off` 会在数据库和 `/kalico alerts` 中标为“已过滤”，不会虚计“已投递”。关闭通知不关闭打印状态跟踪。定时报告、停滞及喷嘴力监控仍需各自启用。喷嘴力事件依赖兼容的状态对象，任意自定义 Kalico 内部事件尚不能自动接入。

```bash
.venv/bin/kalico-monitor config --show
.venv/bin/kalico-monitor config --check
.venv/bin/kalico-monitor config --apply
.venv/bin/kalico-monitor events
.venv/bin/kalico-monitor events --watch
.venv/bin/kalico-monitor discover
.venv/bin/kalico-monitor test paused
```

- `config --apply` 请求确认后同步 Hermes 路由并重启服务；普通 config 向导只保存本地文件。
- `events` 列出注册事件及策略；`events --watch` 观察原始 Moonraker WebSocket 消息；`discover` 查询状态对象，不是 Kalico 内部事件注册表。
- `test paused` 按策略实际发送标记为模拟的通知，可能调用 LLM；不修改打印机或生产队列。Agent accepted 仅表示受理。
- `bash install.sh --reconfigure`：14 逐事件策略，15 直推会话 ID，16 分析会话 ID，11 确认保存。菜单 15/16 输入 `-` 清除显式 chat_id，恢复继承 Hermes 默认群；其余填写 `oc_...` 会同步到对应 Webhook 路由。`KALICO_ALLOWED_CHAT_IDS` 仅限制入站查询权限。
- PR #20 提供回滚、旧版 SQLite 和禁用重试回归测试；仍需人工验证插件更新及真实飞书送达。手工编辑 CFG 后使用 apply 以同步 Hermes 路由，不能只重启服务来同步飞书目标。生成的 CFG 默认仅写 `default: direct`，未单独指定的事件继承默认策略。


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

## /kalico status 显示字段（新功能）

`/kalico status` 支持从 `monitor.env` 读取字段列表，默认按以下顺序显示本次选择的八项：

```ini
KALICO_STATUS_FIELDS=state,file,progress,nozzle,bed,chamber,elapsed,remaining
KALICO_CHAMBER_OBJECT=
```

| 字段名 | 显示内容 | Moonraker 来源 |
| --- | --- | --- |
| `state` | 打印与 Klippy 状态 | `print_stats.state`、`webhooks.state` |
| `file` | 当前 G-code 文件名 | `print_stats.filename` |
| `progress` | 文件位置进度 | `virtual_sdcard.progress` |
| `nozzle` | 喷嘴当前/目标温度 | `extruder` |
| `bed` | 热床当前/目标温度 | `heater_bed` |
| `chamber` | 腔体当前温度 | 可选 `temperature_sensor`、`heater_generic`、`temperature_fan` |
| `elapsed` | 已打印时间 | `print_stats.print_duration` |
| `remaining` | 预计剩余时间（粗略估算） | `print_duration × (1 - progress) / progress` |

`remaining` 仅在 `print_stats.state=printing`、`print_duration > 0`、进度处于 5%–100%（不含 100%）时显示估算；它来自 G-code 文件位置的线性估算，**不是 Orca 等切片器的预计打印时长，不能代表真实剩余时间**。暂停、空闲或数据不足时显示 `—`。如果喷嘴速度变化很大、换层、升温等待等，偏差可能显著。

腔体温度不假设固定对象名称。留空 `KALICO_CHAMBER_OBJECT` 时，插件会查询 Moonraker 的 `/printer/objects/list`，自动寻找名称包含 `chamber`、`enclosure` 或“腔体”的温度对象；若自动发现失败则显示 `—`，不影响基础状态。若你的 printer.cfg 传感器名称不同，设置为实际 Moonraker 对象名，例如：

```ini
KALICO_CHAMBER_OBJECT="temperature_sensor chamber"
```

对象名称必须与 Moonraker `printer.objects.list` 返回值一致，不支持任意 G-code 或执行表达式。如果只需要其他字段，可取消 `chamber`，避免每次查询额外访问腔体对象。

交互式修改：

```bash
cd ~/hermes-plugin-kalico-monitor
bash install.sh --reconfigure
```

在菜单中选择 **[12] /kalico status 选择显示字段**，输入对应编号勾选/取消，输入 **s** 保存当前选择；**[13] 腔体温度对象**允许指定传感器名称，输入 `-` 恢复自动发现。最终通过 **[11] 确认并保存** 才写入配置。中途选择取消不会更改现有配置，Hermes 已安装插件应更新到包含此功能的版本。

快捷命令从共享 `monitor.env` 动态读取显示设置；如 Hermes 和 Monitor 分属不同用户/主机，需要单独保证文件可读性与路径同步。修改的只是命令显示设置，定时报告、事件路由与 Monitor 告警逻辑不受影响。

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
