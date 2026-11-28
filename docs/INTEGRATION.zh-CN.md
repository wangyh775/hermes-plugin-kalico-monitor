# 系统集成

简体中文 | [English](INTEGRATION.md) · [首页](../README.zh-CN.md)

> 依据 main 提交 0c47b84（PR #21 已合并）。Monitor 只读，打印机—Hermes—飞书实机验收尚未完成。

## filament_force

打印机安装原生模块并启用 event_enabled；Monitor 开启 watch_nozzle_force（默认关闭）。桥接先尝试两个可选对象，拒绝时依次仅新、仅旧、基础订阅。消费 `filament_force.events`；旧 `/kalico force` 仍查询旧对象。新插件不依赖 Kalico PR #13。

合法 schema-1 事件按 boot_id:seq 持久化去重，不依赖 Monitor 任务代次。负载保存 alerts.payload，签名 Webhook JSON 携带 `event`。直推使用 detail；需在实际 Hermes Agent 模板中读取 event。默认直推或 Agent 接收不代表已生成曲线或分析。插件 RAM 仅保留 16 条事件，长断线/重启可能丢失未读事件。受力为保峰降采样，非全量 ADC。

## Hermes 与 Kiln

Monitor 仅路由。Hermes Gateway 负责平台授权、消息投递与可选推理。Kiln 或 Moonraker 动作 API 属于独立集成；Monitor 不注册或调用其恢复打印。自动动作应作为有明确安全策略的独立开发项目。

## 验证

1. discover 确认 filament_force，JSON-RPC 订阅。
2. 监督安全模拟故障，检查 event_id、来源、窗口样本、关联 ID 与普通 paused。
3. 检查 SQLite、签名 Webhook 和实际群消息；重连验证不重复。不要为通知测试制造真实过载。

```json
{"jsonrpc":"2.0","method":"printer.objects.subscribe","params":{"objects":{"filament_force":["events"]}},"id":1}
```

生产方协议：[filament_force 事件说明](https://github.com/wangyh775/klipper-filament-force/blob/main/docs/events.md)。
