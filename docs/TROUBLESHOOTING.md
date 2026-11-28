# Troubleshooting

English | [简体中文](TROUBLESHOOTING.zh-CN.md) · [Home](../README.md)

> Reference: main commit 0c47b84 (PR #21 merged). Read-only monitor; live printer–Hermes–Feishu acceptance outstanding.

| Symptom | Check |
|---|---|
| No service at boot | systemctl --user status kalico-monitor; Linger; install.sh --check-autostart |
| No /kalico | Hermes plugin enabled and installed copy updated; reload Gateway; chat authorization |
| No status | Moonraker origin, HTTP/WS reachability, API key, discover |
| Force events absent | watch_nozzle_force enabled; filament_force.events; optional-object fallback; 16-event RAM retention |
| Duplicate notifications | Distinguish paused and filament_jam_detected; both routes; compare event_id / route request IDs |
| HTTP 502 / no chat_id | Hermes Gateway logs, target IDs and default inheritance; test route |
| Agent accepted but no chat | Admission is not delivery; inspect agent run and permissions |
| Payload not visible in chat | detail is default text; template must explicitly use event for analysis |
| Pending queue | Check service journal, route policy, endpoint, signature/clock; don't delete DB without backup |

Run bash install.sh --check, journalctl --user -u kalico-monitor -n 100, and appropriate Hermes gateway logs. Redact keys.
