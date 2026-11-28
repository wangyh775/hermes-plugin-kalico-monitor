# 事件接口

简体中文 | [English](EVENTS.md) · [首页](../README.zh-CN.md)

> 依据 main 提交 0c47b84（PR #21 已合并）。Monitor 只读，打印机—Hermes—飞书实机验收尚未完成。

下列为 Monitor 路由名称，并非全部是 Moonraker 原生方法。生命周期由 print_stats 推导；连接通知和超时独立；受力事件携带生产方负载，暂停独立。

| kind | 触发 | 级别 |
|---|---|---|
| `started` | 状态推导开始 | info |
| `paused` | 打印转暂停 | warning |
| `resumed` | 暂停转打印 | info |
| `complete` | 打印完成 | info |
| `cancelled` | 取消打印 | info |
| `error` | 打印错误 | critical |
| `stalled` | 配置的停滞 | warning |
| `print_progress` | 定时报告 | info |
| `klippy_shutdown` | 固件停机 | critical |
| `klippy_disconnected` | 固件确认断连 | critical |
| `klippy_recovered` | 固件恢复 | info |
| `moonraker_unreachable` | Moonraker 不可达 | critical |
| `moonraker_recovered` | Moonraker 恢复 | info |
| `nozzle_force_anomaly` | 旧回弹异常 | warning |
| `filament_jam_detected` | 疑似堵头 | warning |
| `filament_runout_detected` | 断料/送料异常 | warning |
| `filament_diagnosis_result` | 浮空诊断结果 | info |
| `filament_jam_confirmed` | 重复高阻力 | critical |
| `filament_z_adjusted` | 补偿偏移确认 | info |
| `filament_recovery_started` | 恢复验证 | info |
| `filament_recovery_verified` | 健康窗口 | info |
| `filament_recovery_failed` | 已覆盖失败路径 | warning |

Webhook 外层：printer、kind、severity、filename、detail、occurred_at、alert_id，可选 event（新受力事件）。occurred_at 是本地接收/入库时间，不是 MCU 时间。HMAC-SHA256 对 timestamp + '.' + 原始正文签名；X-Webhook-Timestamp、X-Webhook-Signature-V2、X-Request-ID。源 ID 跨重连去重，投递可重试，不保证传输严格一次。Agent 接收不等于最终投递。

---

## 既有部署细节与历史说明（保留）

以下为原有详细说明；若旧版本描述与前面当前源码基准冲突，以前面为准。

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
| `nozzle_force_anomaly` | 自定义喷嘴力模块触发 | warning |

`stalled` 默认关闭；`print_progress` 默认关闭；`nozzle_force_anomaly` 桥接默认关闭。G-code 文本中的 `Bad probe`、`Probing failed` 目前仅作为近期故障上下文，**不会单独触发告警**。

## 自定义 Kalico PR #13

[Kalico PR #13](https://github.com/wangyh775/kalico/pull/13) 是实验性 Draft。Klippy 模块分析称重传感器回弹事件，按配置选择仅记录、首层 Z 修正或请求暂停。Monitor 通过 Moonraker 订阅 `nozzle_force_monitor` 的 `get_status()`，根据 `triggered` 与 `trigger_count` 变化生成 `nozzle_force_anomaly`，并将事件写入 SQLite。

**与最新 PR #13 的兼容性边界**：`first_layer_action` 支持 `log`、`pause`、`z_adjust`，`normal_layer_action` 支持 `log`、`pause`。当前 Hermes 桥接的 `trigger_count` 主要在请求暂停时递增；成功执行首层 Z 修正或仅记录回弹时，**不会自动发送独立的飞书告警**。虽然 Klippy 状态提供 `control_state`、`adjust_count`、`auto_z_total`、`offset_conflict`、`last_action_reason` 等字段，现有 `/kalico force` 尚未完整展示这些首层控制数据。

Monitor 不能把 Klippy 内部 `printer.send_event` 直接当作 Moonraker 外部事件；当前桥接只使用状态对象订阅。断线、重启去重依赖计数和锁存检查点，尚无跨 Klippy 重启的全局唯一事件 ID 保证。若 `action: pause` 附近出现普通暂停，Monitor 会尽量抑制重复提醒；不能把暂停请求直接说成暂停成功，应另行核查 `print_stats.state=paused`。

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

目前尚无通用 `events.yaml`、每事件独立开关/等级/渠道/冷却规则、首层 Z 修正独立事件、多打印机聚合或 AI 自动控制。这些属于未来功能，不能把规划配置当作已实现功能。

## filament_force 事件（新插件）

开启已有的 KALICO_WATCH_NOZZLE_FORCE=1（可由 monitor.cfg 的 watch_nozzle_force 配置），Monitor 优先同时订阅 nozzle_force_monitor 与 filament_force.events，兼容仅存在其一或均不存在的设备。不会发送 G-code。

支持 filament_jam_detected、filament_runout_detected、filament_diagnosis_result、filament_jam_confirmed、filament_z_adjusted、filament_recovery_started、filament_recovery_verified、filament_recovery_failed。各事件可配置 direct/agent/both/off；普通 paused/resumed 等生命周期事件仍独立发送。源事件 ID 由 Klippy boot_id 与序号组成，SQLite 去重，完整传输 payload 保存到 alerts.payload 并通过签名 Webhook JSON 的 event 字段发送；直接通知模板仍使用简短 detail，代理路由可使用 event 数据（需对应 Hermes 模板）。堵头窗口为保峰降采样，不是全速原始 ADC 文件。

Klippy 仅保存最近 16 条内存事件；断线过久或 Klippy 重启可能丢失未接收事件。Monitor 收到后持久化。协议见 [filament_force 事件文档](https://github.com/wangyh775/klipper-filament-force/blob/main/docs/events.md)。
