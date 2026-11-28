# 事件生成、通知字段与路由（Monitor CFG）

本页适用于本 PR 合并后的版本。Monitor **只读采集、生成并路由事件**，不调用 Kiln、不执行 G-code、不修改打印机状态。人类/智能体通过 Kiln 或 Moonraker API 执行何种动作，属于后续独立的授权和编排层。

## 事件来源分类

1. **状态派生**：Monitor 比较 Moonraker 的 `print_stats.state` / `webhooks.state`，产生 `started`、`paused`、`resumed`、`complete`、`klippy_shutdown` 等。它们不是 Moonraker 发来的同名 Webhook。
2. **插件结构化**：Kalico 的 `filament_force.events` 发布诊断、堵头、Z 补偿等事件。启用 `[monitor] watch_nozzle_force: 1` 后只订阅新 `filament_force`；旧 `nozzle_force_anomaly` 已停止生成。
3. **用户自定义数值规则**：由 `[event NAME]` 检查已存在的 Moonraker 状态对象，持续满足阈值后生成事件。它不是 Klippy 插件注册机制，也不执行任何打印动作。

`[events]` 只管分发：`off`（过滤）/ `direct`（无 LLM）/ `agent`（Hermes 分析）/ `both`（两路），对内置事件与规则事件同样有效。自定义规则自身的 `mode` 是默认投递方式；`[events]` 中同名配置优先。

## 自定义 Direct 发送字段

修改 `~/.config/kalico-monitor/monitor.cfg`：

```ini
[message]
fields: printer,kind,severity,occurred_at,filename,detail,alert_id

[message print_progress]
fields: printer,filename,progress,elapsed,remaining,nozzle,bed,detail

[message filament_jam_confirmed]
fields: printer,kind,layer,force_g,diagnostic_speed_mm_s,result,reason,detail
```

- `[message]` 是所有未单独配置事件的字段顺序。
- `[message KIND]` 覆盖某一种事件。字段按配置顺序渲染。
- 支持字段：`printer,kind,severity,occurred_at,filename,detail,alert_id,progress,elapsed,remaining,nozzle,bed,chamber,layer,total_layers,force_g,e_speed_mm_s,diagnostic_speed_mm_s,result,reason,step_mm,total_mm,window_summary,object,field,value,threshold`。
- 只有事件实际包含的数据才能显示。例如常规 `paused` 不含受力窗口；不可用值显示为“—”，不会猜测。
- 此处配置的是告警 **Direct** 内容，不影响 `/kalico status` 的 `[status] fields`，也不决定 Agent 的推理提示词。
- Hermes Direct 路由新增 `{message}` 模板变量。**升级后需在可信环境执行一次 `.venv/bin/kalico-monitor config --apply`，按提示确认同步 Hermes 模板，并检查 Gateway 状态。** 如果只重启 Monitor、未同步 Hermes 路由，旧模板仍会使用原先的固定字段，不会采用新的消息呈现格式。

## 自定义 Moonraker 阈值事件

```ini
[event chamber_temperature_high]
source: moonraker
object: temperature_sensor chamber
field: temperature
condition: gt
threshold: 150
duration: 10
cooldown: 300
severity: warning
mode: both

[events]
default: direct
chamber_temperature_high: direct
```

参数含义：

- `source`：当前只能填写 `moonraker`。
- `object`：完整的 Moonraker 状态对象名称，必须出现在 `printer.objects.list`。不支持任意 Python 表达式。
- `field`：对象中的数值字段，支持 `temperature` 等简单字段及 `a.b` 形式的嵌套字段。
- `condition`：`gt` (>), `ge` (>=), `lt` (<), `le` (<=)。
- `threshold`：有限数值；`duration`：持续达到阈值的秒数（0–86400）；`cooldown`：同一规则两次告警的最短间隔秒数（0–86400）。
- `severity`：`info` / `warning` / `critical`；`mode`：此规则的默认消息路由。
- 规则只在数值有效且当前连接可用时判断，离线或不满足条件会解除持续计时。每次满足→不满足→再次满足才可重新触发，并遵守冷却时间。
- 规则状态及告警持久化在 SQLite，重启后不会因为单次状态订阅重复触发。最大允许 32 条自定义规则。
- 如果可选对象不存在或订阅遭拒，Monitor 保持基础订阅并记录警告；此规则无法触发，不能据此认定温度安全。
- 上例中的 **150℃ 仅用于演示语法，不是安全阈值建议**。高温设备的过温、断水保护必须在硬件/Kalico 本地实现。

对于喷嘴力堵头，优先消费新 `filament_force` 的诊断事件，而不要在 Monitor 重写堵头算法。只有新模块明确暴露数值字段、且确有独立监控需求时，才另建数值规则。

## 应用与验证

```bash
cd ~/hermes-plugin-kalico-monitor
.venv/bin/kalico-monitor config --check
.venv/bin/kalico-monitor events
.venv/bin/kalico-monitor discover
systemctl --user restart kalico-monitor
# 仅 Direct 消息模板或群路由需要同步时：
.venv/bin/kalico-monitor config --apply
bash install.sh --check
.venv/bin/kalico-monitor test print_progress
```

`config --check` 校验配置（含自定义节）；`events` 显示注册事件与规则事件；`discover` 列出实际状态对象；`test` 发送**模拟** Webhook，不能代替真实传感器持续条件验证。发送响应 `direct delivered` 是 Hermes 的确认，最终还应查看飞书实际收到的内容。

已保存的旧 `nozzle_force_anomaly` 告警历史保留在 SQLite 审计数据中，不会再生成新告警；如果用户原来的 `[events]` 仍有这个名字，只是无来源的路由条目，建议删除。

**边界：** Monitor 从不调用 Kiln 或 Moonraker 的打印机动作接口。后续人类/Agent 决策必须有单独的身份校验、权限控制、显式确认、幂等性及审计记录。
