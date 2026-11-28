# 第三轮可靠性专项修复

基于 PR #28，不新增事件处理、Skill 或打印机控制。关键告警附加数据
无法编码时隔离原数据，并以原事件 ID 保存最小载荷。WriteResult 区分
inserted、duplicate、degraded、quarantined；原 enqueue 接口兼容返回 ID。
Ready 状态、恢复事件与 Incident 关闭在同一事务提交。
历史故障关联键仅在可证明的事件类型和 ID 下回填，无法确认的记录保留原状。

连接代次校验拒绝已经失效连接的迟到数据。传输连接、RPC 响应和订阅状态
独立展示；RPC 可用但订阅超时时进入降级重订阅。HTTP 400、413、422
作为永久拒绝进入死信，其他远端错误继续重试；恢复消息提示此前告警可能
未送达。Agent 确认顺序不等于推理完成顺序。外部错误文本限制长度并安全编码。

重复插件日志快照跳过，已持久化的事件不重复写事务；迟到缺失事件仍可入库。
新任务与 resumed 互斥。SQLite 维护与看门狗分开调度，每批 100 条，
锁等待 250 ms；同步磁盘 I/O 仍可能阻塞，没有独立写线程或硬容量上限。
锁超时属于系统级失败，不会伪造成功提交。

可选 monitor.cfg：
```ini
[reliability]
subscription_refresh: 30
subscription_timeout: 20
observation_gap: 35
field_max_age: 120
connect_timeout: 5

[status]
chamber_object: temperature_sensor chamber
```
最大观测间隔必须大于刷新周期且不超过字段有效期。显式腔体对象在可用时
订阅。健康检查显示 Webhook 最近应答及最早积压时间，超过 300 秒的应答
标为历史状态。源时间仍允许为空，不猜测绝对时间。

只读诊断命令：
```sh
kalico-monitor dead-letters list
kalico-monitor dead-letters show EVENT_ID
kalico-monitor incidents list
kalico-monitor storage status
```
不自动重投或删除死信。命令依赖已迁移数据库与正常配置。
专项测试覆盖事务回滚、降级 ID、关联键迁移、并发读及锁等待。
部署前运行 pytest、ruff；CI 不是 Jetson 实机验收。
