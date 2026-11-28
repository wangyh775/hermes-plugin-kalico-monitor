# 配置参考

简体中文 | [English](CONFIGURATION.md) · [首页](../README.zh-CN.md)

> 依据 main 提交 0c47b84（PR #21 已合并）。Monitor 只读，打印机—Hermes—飞书实机验收尚未完成。

`monitor.cfg` 是独立 INI，通常位于 ~/.config/kalico-monitor/monitor.cfg，与 monitor.env 同目录；覆盖对应 ENV 业务项。密钥/API Key 与 Webhook URL 留在 ENV。允许节 monitor、printer、status、hermes、events；未知节/参数报错，不执行 G-code 或任意表达式。示例（合入现有文件）：

```ini
[monitor]
progress_report_minutes: 0
stall_minutes: 0
disconnect_seconds: 25
watch_nozzle_force: 1
[printer]
name: PEEK-01
moonraker_url: http://192.168.1.100:7125
[status]
fields: state,file,progress,nozzle,bed,chamber,elapsed,remaining
chamber_object:
[events]
default: direct
filament_jam_confirmed: both
```

示例 IP。通过向导配置 [hermes] direct_chat_id、agent_chat_id；空值继承默认群。菜单 15/16 输入 `-` 清除显式群。both 前必须配置 Agent。状态默认字段如上；预计剩余为 duration×(1−progress)/progress，不是切片器估计。

| ENV | 默认 / 约束 |
|---|---|
| `KALICO_PRINTER_NAME` | PEEK-01 |
| `MOONRAKER_URL` | http://127.0.0.1:7125 |
| `MOONRAKER_API_KEY` | 未要求时留空 |
| `HERMES_WEBHOOK_URL` | http://127.0.0.1:8644/webhooks/kalico-alert |
| `HERMES_AGENT_WEBHOOK_URL` | 空；非本机 HTTPS |
| `HERMES_WEBHOOK_SECRET` | 必填随机密钥 |
| `KALICO_EVENT_MODES` | default=direct; off/direct/agent/both |
| `KALICO_MONITOR_DB` | ~/.local/state/kalico-monitor/alerts.sqlite3 |
| `KALICO_DISCONNECT_SECONDS` | 25; ≥5 s |
| `KALICO_STALL_MINUTES` | 0 (off) |
| `KALICO_PROGRESS_REPORT_MINUTES` | 0 (off); 5–1440 min |
| `KALICO_WATCH_NOZZLE_FORCE` | 0 (off); legacy/new optional objects |
| `KALICO_ALLOWED_CHAT_IDS` | 可选额外入站限制 |
| `KALICO_STATUS_FIELDS` | state,file,progress,nozzle,bed,chamber,elapsed,remaining |
| `KALICO_CHAMBER_OBJECT` | 空；自动发现 |
| `KALICO_MONITOR_ENV_FILE` | ~/.config/kalico-monitor/monitor.env |
| `KALICO_MONITOR_CFG` | 可选显式路径 |

config --check 校验；config --apply 确认后同步集成及重启。编辑前备份。不要把 HMAC 密钥放入截图/日志。


## 配置优先级

CFG 显式业务项优先于旧 ENV，缺失项继承 ENV；安装器仍可能同步部分字段到 ENV。应用前使用 config --show、config --check 验证生效配置。配置事件名称不等于创建检测规则。若只使用新版插件，保留 watch_nozzle_force 并设置 nozzle_force_anomaly: off；该开关会订阅两个可选对象，不创建检测器。

当前配置菜单支持事件路由和独立 direct/agent 目标。应用前备份 monitor.env、monitor.cfg 及 Hermes 配置；API Key、Webhook 密钥和 URL 留在 ENV。部署细节参见[一键安装指南](ONE_CLICK_INSTALL.zh-CN.md)，当前配置参考优先。
