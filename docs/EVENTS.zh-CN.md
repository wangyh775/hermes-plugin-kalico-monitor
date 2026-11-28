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


## 生产方接口约定

参见[生产方协议](https://github.com/wangyh775/klipper-filament-force/blob/main/docs/events.md)。嵌套 event 为原始 schema-1 字典，不以 Monitor 任务代次替代源 event_id。初始 incident_id=null，后续关联初始 event_id。source 为 band、hard、model_validation；force_g 在 band/model_validation 下为窗口特征，hard 下为触发采样相对基准绝对力，不一定是峰值。window.samples 为 [相对 MCU print-time 秒, 有符号标定 gf]，未减基准；window.force_ref_g 可为空。快照在主机故障处理生成，并非精确物理触发时刻；统计窗口独立保留。诊断脉冲均值/峰值为数组，improvement 可为空或超出 0–1。FAILED 不一定发 recovery_failed，还需查询打印机状态。

| Webhook 字段 | 类型 | 含义 |
|---|---|---|
| printer、kind、severity、filename、detail | 字符串 | Monitor 路由及文本 |
| occurred_at | 字符串 | 本地入库时间，无时区偏移 |
| alert_id | 字符串 | Monitor 告警 ID |
| event | 对象，可选 | 源 schema-1 事件，含适用的受力窗口 |

直推成功响应为 delivered/duplicate；Agent 为 accepted/duplicate（仅接收）。X-Request-ID = alert_id + :direct 或 :agent；HMAC-SHA256 十六进制签名针对 UTF-8 时间戳 + "." + 原始正文。双路独立状态与重试。不得仅凭事件到达就执行物理动作。CLI 模拟通知不包含源结构化事件，[模拟 JSON](examples/jam-event.json) 可用于离线接口测试。
