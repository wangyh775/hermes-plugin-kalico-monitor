# Configuration

English | [简体中文](CONFIGURATION.zh-CN.md) · [Home](../README.md)

> Reference: main commit 0c47b84 (PR #21 merged). Read-only monitor; live printer–Hermes–Feishu acceptance outstanding.

`monitor.cfg` is an independent INI file, normally next to monitor.env at ~/.config/kalico-monitor/monitor.cfg. It overrides corresponding ENV business settings. Secrets/API keys and webhook URLs remain in ENV. Accepted sections: monitor, printer, status, hermes, events. Unknown sections/options fail; no G-code or arbitrary expressions. Example (merge with existing file):

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

Example IP only. Configure [hermes] direct_chat_id and agent_chat_id through the wizard; empty inherits default. Menu 15/16 `-` clears explicit targets. Agent endpoint must be configured before both. Default status fields as above; estimated remaining time is duration×(1−progress)/progress, not slicer estimate.

| ENV | Default / constraints |
|---|---|
| `KALICO_PRINTER_NAME` | PEEK-01 |
| `MOONRAKER_URL` | http://127.0.0.1:7125 |
| `MOONRAKER_API_KEY` | Empty if not required |
| `HERMES_WEBHOOK_URL` | http://127.0.0.1:8644/webhooks/kalico-alert |
| `HERMES_AGENT_WEBHOOK_URL` | Empty; HTTPS for non-local |
| `HERMES_WEBHOOK_SECRET` | Required random secret |
| `KALICO_EVENT_MODES` | default=direct; off/direct/agent/both |
| `KALICO_MONITOR_DB` | ~/.local/state/kalico-monitor/alerts.sqlite3 |
| `KALICO_DISCONNECT_SECONDS` | 25; ≥5 s |
| `KALICO_STALL_MINUTES` | 0 (off) |
| `KALICO_PROGRESS_REPORT_MINUTES` | 0 (off); 5–1440 min |
| `KALICO_WATCH_NOZZLE_FORCE` | 0 (off); legacy/new optional objects |
| `KALICO_ALLOWED_CHAT_IDS` | Optional extra inbound restriction |
| `KALICO_STATUS_FIELDS` | state,file,progress,nozzle,bed,chamber,elapsed,remaining |
| `KALICO_CHAMBER_OBJECT` | Empty; auto discovery |
| `KALICO_MONITOR_ENV_FILE` | ~/.config/kalico-monitor/monitor.env |
| `KALICO_MONITOR_CFG` | Optional explicit path |

Validate with config --check; config --apply asks confirmation and synchronizes integration/restarts. Back up before editing. Do not put HMAC secrets into screenshots or logs.


## Configuration precedence

Explicit CFG business options override legacy ENV options; missing options inherit ENV. The installer may mirror fields into ENV for backward compatibility. Use config --show and config --check to verify the effective configuration before applying. Do not mistake a routing name for an implemented event rule. To disable legacy force notifications while retaining new plugin events, enable watch_nozzle_force and set nozzle_force_anomaly: off. The switch subscribes to both optional object types; it is not a new force detector.

The current configuration menu includes event routing and separate direct/agent targets. Back up monitor.env, monitor.cfg and Hermes config before applying. Keep the API key, webhook secret and URL in ENV. For detailed installer procedures see [deployment guide](ONE_CLICK_INSTALL.zh-CN.md) (Chinese legacy deployment detail; the current configuration reference takes precedence).
