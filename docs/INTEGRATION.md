# Integration

English | [简体中文](INTEGRATION.zh-CN.md) · [Home](../README.md)

> Reference: main commit 0c47b84 (PR #21 merged). Read-only monitor; live printer–Hermes–Feishu acceptance outstanding.

## filament_force

On the printer, install the native extra and enable event_enabled. On Monitor enable watch_nozzle_force (default off). The bridge tries both optional objects, then new-only, legacy-only and base subscription if rejected. It consumes only `filament_force.events`; old `/kalico force` still queries the legacy object. A new plugin does not require Kalico PR #13.

Each valid schema-1 event is deduplicated durably by boot_id:seq, independent of Monitor job generation. Received payload is stored in alerts.payload; the signed Webhook JSON includes `event`. Direct messages use detail; configure the actual Hermes agent template to consume event. Neither default direct templates nor agent admission prove waveform rendering or analysis. The plugin retains 16 recent events in RAM; long outage or restart may lose unread events. Force samples are peak-preserving reduced data, not full ADC capture.

## Hermes and Kiln

Monitor routes events only. Hermes Gateway handles platform authorization, delivery and optional model analysis. Kiln or Moonraker action APIs are separate integrations; Monitor does not register or call them for recovery. Treat automated actuation as a distinct project with explicit safety policy.

## Verify

1. Discover objects and check filament_force exists; subscribe via JSON-RPC.
2. Use safe supervised fault simulation; inspect event_id, source, window samples, incident linkage and lifecycle pause.
3. Check SQLite and signed webhook receipt; verify real chat delivery. Reconnect and confirm no duplicate event IDs. Do not create a physical overload for a notification test.

```json
{"jsonrpc":"2.0","method":"printer.objects.subscribe","params":{"objects":{"filament_force":["events"]}},"id":1}
```

Producer specification: [filament_force event protocol](https://github.com/wangyh775/klipper-filament-force/blob/main/docs/events.md).
