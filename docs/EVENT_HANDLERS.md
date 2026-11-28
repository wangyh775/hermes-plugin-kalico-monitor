# Event handlers / 事件处理宏

This phase adds per-event read-only Agent workflows, using Hermes native
Webhook prompt and skills. Monitor generates and routes events; Hermes
performs inference. Neither Monitor nor these generated routes grants
Kiln/Moonraker write access.

本阶段新增事件处理宏。Monitor 只负责采集、生成与路由，Hermes 执行推理，
生成的路由仅授权 kalico_monitor 只读工具集，不授权打印机动作。

```ini
[events]
default: direct
filament_jam_confirmed: both
klippy_shutdown: agent

[handler FILAMENT_JAM]
skills: kalico-filament-diagnosis
prompt:
    分析堵头事件 {kind}，检查二次诊断数据 {event.result}。
    事件数据：{event}
    仅提供原因、置信度和处理建议，不执行动作。

[handler SYSTEM_DIAGNOSIS]
prompt:
    根据 {detail} 和实时状态定位 Klippy 异常，给出排查步骤。

[handlers]
filament_jam_confirmed: FILAMENT_JAM
klippy_shutdown: SYSTEM_DIAGNOSIS

[message filament_jam_confirmed]
template:
    【堵头告警】
    设备：{printer}
    挤出力：{force_g} gf
    结果：{result}
```

Use names of trusted Skills already installed in Hermes. This release does not
install Skills. A handler binding only affects the agent leg of agent/both.
Unbound events use kalico-analysis; default binding is optional, and an
explicit off binding opts out of the custom handler (not out of Agent delivery).
Set [events] kind: off/direct to disable inference.

Skill 必须预先安装；绑定仅在 agent/both 时生效。未绑定沿用默认分析；
[handlers] default 可设置默认处理宏；某事件绑定 off 表示回退默认分析，
不是关闭推理。关闭推理请使用 [events] 的 off/direct。

Direct fields and template are mutually exclusive per section.
Per-event sections override the default. Missing scalar fields become —;
no Python/Jinja execution or recursive substitution. Agent supports Hermes
top-level and event dotted placeholders. Large raw windows may be truncated
by Hermes; full event payload remains in SQLite. Native Hermes version and
Skill availability must be checked on the target device.

```bash
kalico-monitor config --check
kalico-monitor handlers
kalico-monitor handlers --info FILAMENT_JAM
kalico-monitor events --info filament_jam_confirmed
kalico-monitor config --apply
kalico-monitor test filament_jam_confirmed
```

Apply synchronizes versioned managed routes, preserving unrelated Hermes
routes and inheriting the Agent Feishu target. Editing configuration changes
the generated route ID; apply is required before delivery succeeds. No silent
fallback to the generic Agent route for a configured handler. Pending retries
use the active configuration at dispatch time, not an immutable handler snapshot.
Webhook accepted means queued for inference, not completed. Tests are synthetic.

配置修改后必须 config --apply 同步并重启；不静默回退至统一 Agent 路由。
待重试消息使用当前有效配置；未实现处理结果回调、跨阶段关联或动作执行。

The offline installer self-check (`bash install.sh --check`) compares generated
routes with local configuration; it does not establish Skill existence or a live
Agent response. 安装自检检查静态路由同步，不代表实机推理或飞书送达。
