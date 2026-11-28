# 事件与只读命令

本页以当前 main 代码为准。**Kalico 本地负责控制；Monitor 只读识别事件；Hermes 负责投递。**


## PR #19 开发预览

新增 CFG 配置、四种事件路由、飞书双路目标、事件列表与观察、模拟通知及配置应用命令。完整参数、命令和限制见[新版配置参考](CONFIGURATION.zh-CN.md#pr-19cfg-配置与事件管理开发预览)。当前为 Draft，尚未完成 CI 与实机验收；下文旧版 ENV 说明仍适用于主分支。


## 事件清单

| kind | 触发条件 | 级别 |
| --- | --- | --- |
| `complete` | 打印完成 | info |
| `cancelled` | 打印取消 | info |
| `paused` | 打印暂停 | warning |
| `error` | 打印错误 | critical |
| `klippy_shutdown` | Klippy Shutdown | critical |
| `klippy_disconnected` | Klippy 断连超出确认窗口 | critical |
| `klippy_recovered` | Klippy 恢复连接 | info |
| `moonraker_unreachable` | Moonraker WebSocket 不可达 | critical |
| `moonraker_recovered` | Moonraker 订阅恢复 | info |
| `stalled` | 打印进度长时间不变 | warning |
| `print_progress` | 打印中的定时报告 | info |

`stalled` 默认关闭；`print_progress` 默认关闭；`filament_force` 订阅默认关闭。G-code 文本中的 `Bad probe`、`Probing failed` 目前仅作为近期故障上下文，**不会单独触发告警**。

## 事件来源说明

`started`、`paused`、`resumed` 是 Monitor 根据 Moonraker 状态转移**派生**的事件，不是同名 Moonraker 原生 Webhook。`filament_force.events` 是 Klippy 插件的结构化事件日志；`[event NAME]` 则允许 Monitor 根据 Moonraker 的数值状态生成只读阈值事件。详见 [事件配置](EVENT_ROUTER.zh-CN.md)。

## 零 LLM 快捷命令

| 命令 | 作用 |
| --- | --- |
| `/kalico`、`/kalico status` | 打印状态、文件、进度、温度 |
| `/kalico temp` | 喷嘴、热床实际及目标温度 |
| `/kalico force` | 自定义力状态、回弹计数、阈值 |
| `/kalico alerts` | 最近 5 条非定时告警及待投递数量 |
| `/kalico health` | Monitor 心跳及队列长度 |
| `/kalico help` | 帮助 |

所有远程快捷命令需要 `KALICO_ALLOWED_CHAT_IDS` 授权，且不执行 G-code。插件另外提供 `kalico_status`、`kalico_force`、`kalico_monitor_health` 三个只读 Agent 工具；Agent 工具调用可能涉及 LLM，**零 LLM 特性仅指斜杠命令与 `deliver_only` 告警**。

## 通知流程与当前限制

```text
Kalico -> Moonraker 状态订阅 -> Monitor 事件识别
       -> SQLite outbox -> HMAC Hermes Webhook -> 飞书
```

只有 Hermes Webhook 返回 `delivered` 或 `duplicate` 才认为投递成功；网络恢复可能补发历史报告。报告进度按文件位置估算，不代表成型质量。

现已支持严格的 `monitor.cfg` 数值阈值规则、Direct 字段选择；不支持任意代码或自动打印机控制。详见 [事件配置](EVENT_ROUTER.zh-CN.md)。

## filament_force 事件（新插件）

开启已有的 KALICO_WATCH_NOZZLE_FORCE=1（可由 monitor.cfg 的 watch_nozzle_force 配置），Monitor 仅订阅 `filament_force.events`，旧 `nozzle_force_monitor` 告警不再产生。不会发送 G-code。

支持 filament_jam_detected、filament_runout_detected、filament_diagnosis_result、filament_jam_confirmed、filament_z_adjusted、filament_recovery_started、filament_recovery_verified、filament_recovery_failed。各事件可配置 direct/agent/both/off；普通 paused/resumed 等生命周期事件仍独立发送。源事件 ID 由 Klippy boot_id 与序号组成，SQLite 去重，完整传输 payload 保存到 alerts.payload 并通过签名 Webhook JSON 的 event 字段发送；直接通知模板仍使用简短 detail，代理路由可使用 event 数据（需对应 Hermes 模板）。堵头窗口为保峰降采样，不是全速原始 ADC 文件。

Klippy 仅保存最近 16 条内存事件；断线过久或 Klippy 重启可能丢失未接收事件。Monitor 收到后持久化。协议见 [filament_force 事件文档](https://github.com/wangyh775/klipper-filament-force/blob/feature/diagnostic-recovery-fsm/docs/events.md)。
