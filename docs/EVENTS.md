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


## Producer event contract

See the [producer protocol](https://github.com/wangyh775/klipper-filament-force/blob/main/docs/events.md). The nested event is the original schema-1 dictionary; do not substitute Monitor generation for the producer event_id. Initial incident_id is null, follow-up events link to the initial event_id. source is band, hard, or model_validation; force_g is the scored window level for band/model_validation and the baseline-relative absolute trigger reading for hard, not necessarily a peak. window.samples are [relative MCU print-time seconds, signed calibrated gf], not baseline-subtracted. window.force_ref_g may be null. Snapshot timing is host fault processing, not exact physical trigger time. Recent statistical windows have independent retention. Diagnostic pulse means/peaks are arrays, improvement may be null or outside 0–1. Not every FAILED emits recovery_failed; use printer status as well.

| Webhook field | Type | Meaning |
|---|---|---|
| printer, kind, severity, filename, detail | string | Monitor routing and text |
| occurred_at | string | Local receipt/storage time, no timezone offset |
| alert_id | string | Monitor alert identity |
| event | object, optional | Source schema-1 event, including window where present |

Delivery success: direct accepts delivered or duplicate; agent accepts accepted or duplicate (admission only). X-Request-ID = alert_id + :direct or :agent; HMAC-SHA256 hex over UTF-8 timestamp + "." + exact request body. Both routes have independent state and retry. Do not execute physical actions merely because an event arrived. Synthetic CLI test notifications omit the structured source event; a valid sample fixture is supplied in [examples](examples/jam-event.json) for offline integration tests.
