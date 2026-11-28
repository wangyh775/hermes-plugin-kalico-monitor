# 事件可靠性

Monitor 使用路由任务及独立 Direct/Agent Worker；每次按 critical、warning、info 及创建时间重新取一条可投递任务。已在途请求不抢占，但 Agent 等待不阻塞 Direct。任务意外退出、取消或异常将取消同级任务并使服务失败，由 systemd 重启策略恢复。SQLite Outbox 和分路请求 ID 保留。Agent 成功仅表示接收，不代表最终群投递；重试不是严格一次传输。

可选 filament_force 队列原子保存源事件、缺口告警及每打印机/每 boot 高水位游标。首次观察建立边界，不推断此前丢失；同 boot 序号跳跃发 filament_event_gap，携带 missing_from_seq、missing_to_seq、observed_seq、missing_count。迟到重放仍可去重入库，不降低游标。缺口是观察时可能缺失，不证明永久丢失；新 boot 无法证明停机前的尾部状态。内存保留有限，不保证回放已淘汰事件。无效/未知事件跳过并限频记录观测数。新版 filament_sensor_fault 携带 schema-1 看门狗原始负载；兼容旧生产方。recovery_failed 是独立状态转换，可能同时通知。

```ini
[events]
default: direct
filament_sensor_fault: direct
filament_event_gap: direct
filament_recovery_failed: both
```

仅示例，先配置及验证 Agent。Monitor 保持只读。
