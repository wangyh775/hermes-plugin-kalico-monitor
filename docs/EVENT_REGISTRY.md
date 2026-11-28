# Unified event registry / 统一事件注册中心

The registry separates event origin from delivery policy. Existing 21 event names,
SQLite IDs and filament_force payloads are unchanged. Six source categories:
moonraker_native, moonraker_lifecycle, klipper_extension, monitor_internal,
custom_rule, external (reserved; no ingress endpoint yet).

事件注册中心统一事件定义、来源和路由。21 个原有事件名及 SQLite 去重键保持不变。
原生通知目录来自 [Moonraker documentation](https://moonraker.readthedocs.io/en/latest/external_api/jsonrpc_notifications/)；
目录不等于当前服务器支持列表。外部事件类型预留，未开放接收接口。

## Configuration / 配置

```ini
[events]
default: direct
notify_history_changed: both
notify_service_state_changed: direct
notify_cpu_throttled: direct

[message notify_history_changed]
fields: printer,kind,source_type,source_method,detail
```

All raw notifications default to off even if default is both. Ten listed
forwardable methods require explicit opt-in; other methods are catalog-only.
The raw adapter retains positional params up to 16 KiB, at most one event per
method per 30 seconds in a daemon lifetime. This is intentionally lossy, not
a guaranteed event log. Direct output is a short summary; Agent needs an
explicit event-data prompt to interpret params. Raw payloads may contain
untrusted data, so enable selectively. Internal Klippy safety handling and
print lifecycle processing remain separate.

原生通知默认关闭，不继承 default；十种可转发通知需显式配置，其他通知仅目录展示。
每种通知 30 秒最多转发一次（进程内），参数超过 16 KiB 丢弃，不保证原始消息全量保存。
不要把该机制当作实时保护或可靠历史记录。原有 Klippy 告警与打印生命周期不受影响。

## CLI

```bash
kalico-monitor events
kalico-monitor events --native
kalico-monitor events --source klipper_extension
kalico-monitor events --info filament_jam_confirmed
kalico-monitor events --watch
```

Webhook includes schema_version, source_type, source, source_object,
source_method and trigger. SQLite migration adds origin; old rows infer
known category only, not observed transport. Actual trigger details are
recorded where known; null is possible. Existing event payload stays in event.

Webhook 新增来源元数据，SQLite 自动增列 origin，不修改旧事件 payload。
历史记录仅推断已知分类；不能推断的实际触发路径保留 null。
未实现自定义通知匹配、外部事件接收、Handler/Skill 编排或控制动作。
