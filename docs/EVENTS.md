# Events

English | [简体中文](EVENTS.zh-CN.md) · [Home](../README.md)

> Reference: main commit 0c47b84 (PR #21 merged). Read-only monitor; live printer–Hermes–Feishu acceptance outstanding.

The following names are Monitor routing kinds, not all native Moonraker methods. Lifecycle transitions are inferred from print_stats; connection notifications and timeouts are separate. Force events carry the producer payload; a pause remains independent.

| kind | Trigger | Severity |
|---|---|---|
| `started` | Print start inferred from state | info |
| `paused` | Printing to paused | warning |
| `resumed` | Paused to printing | info |
| `complete` | Completed job | info |
| `cancelled` | Cancelled job | info |
| `error` | Print error | critical |
| `stalled` | Configured progress stall | warning |
| `print_progress` | Periodic report | info |
| `klippy_shutdown` | Klippy shutdown | critical |
| `klippy_disconnected` | Confirmed Klippy disconnect | critical |
| `klippy_recovered` | Klippy recovered | info |
| `moonraker_unreachable` | Moonraker unavailable | critical |
| `moonraker_recovered` | Moonraker recovered | info |
| `nozzle_force_anomaly` | Legacy rebound status | warning |
| `filament_jam_detected` | Suspected jam | warning |
| `filament_runout_detected` | Low force | warning |
| `filament_diagnosis_result` | Free-air diagnosis result | info |
| `filament_jam_confirmed` | Repeated high resistance | critical |
| `filament_z_adjusted` | Offset acknowledged | info |
| `filament_recovery_started` | Resume verification | info |
| `filament_recovery_verified` | Healthy windows | info |
| `filament_recovery_failed` | Covered failure path | warning |

Webhook envelope: printer, kind, severity, filename, detail, occurred_at, alert_id, optional event (new force events). occurred_at reflects local receipt/storage time, not MCU sample time. HMAC-SHA256 over timestamp + '.' + exact body; X-Webhook-Timestamp, X-Webhook-Signature-V2, X-Request-ID. Source IDs deduplicate across reconnects; delivery is retryable, not an exactly-once transport guarantee. Agent acceptance is not final delivery.
