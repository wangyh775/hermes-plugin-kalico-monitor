# 事件系统可靠性专项

Monitor 仍只负责事件产生与路由，不新增处理宏、Skill 或打印机动作。
Direct 与 Agent 独立分发，任一任务异常退出均传播至主进程；
两路分别记录心跳，心跳不代表飞书已送达。critical 优先于 warning、info；
每路一个在途请求，不抢占已发请求。超过 30 分钟的待发进度报告过滤。

订阅确认匹配请求 ID，Klippy ready 使旧订阅失效，新订阅单次确认超时 20 秒。
每 30 秒刷新订阅以确认未改变的字段。数值规则要求有效订阅及 120 秒字段新鲜度，
连续观测采用单调时钟，断线与进程重启清除未完成窗口；
已告警阈值保持锁存，直到有效正常值出现。
可选对象缺失、规则字段无效反映在订阅健康状态，
并产生 monitor_degraded / monitor_recovered；恢复不表示能补回所有遗漏事件。

原生事件默认关闭。不同的连续事件内容不再按方法名统一限频。
完全相同且连续的事件在 2 秒内去重（按钮边沿例外），仍非无损日志或严格一次投递。
16 KiB 保守载荷上限，超限丢弃并记录计数。服务状态、CPU 限频位、G-code 响应、
任务及队列字段可用于模板，保留原始 event.params。

## 配置与规范

未知事件、开启不可转发事件会报错。nozzle_force_anomaly: off 提示迁移，请删除；
消息模板引用须为已注册事件、自定义规则或默认模板。
六类来源保持不变，external 尚无接收接口。注册定义集中描述严重等级、默认路由、
Schema 版本、载荷类别及去重策略，原生状态变化可动态调整严重等级。

Webhook 兼容 v1，规范见 event-envelope-v1.schema.json。
occurred_at 保留旧版入队时间语义，queued_at 为 UTC，received_at 为 Monitor 观察时间
（旧数据可能为空），source_occurred_at 无可信源时钟时为空（当前不猜测源时间）。
投递时间在 SQLite 中记录，Agent accepted 不代表推理完成。
已有事件 ID、堵头原始载荷保持兼容。故障事件与关联 ID 一起持久化；
报告间隔变化不再重置同一任务的报告序号。

## 校验

```bash
python -m pytest -q
ruff check .
kalico-monitor config --check
kalico-monitor events --info monitor_degraded
kalico-monitor events --info notify_service_state_changed
```

实机验收需确认 Moonraker 订阅刷新、可选对象、Hermes 与飞书送达、数据库增长、
systemd 重启。/kalico health 显示订阅状态及独立分发心跳。
本模块不是打印机实时安全保护。
