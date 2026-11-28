# Event system reliability

Monitor remains a read-only event producer/router. No new Handler, Skill or
control functionality. The two delivery workers are independently scheduled;
unexpected exit of either worker or the listener propagates to the service
supervisor. Each worker records a heartbeat, not proof of remote delivery.
Critical alerts precede warnings and informational events; each worker has
one in-flight request. Pending progress reports older than 30 minutes expire.

Subscription acknowledgements are matched to request IDs. Klippy ready
invalidates the old subscription; a full acknowledgement must arrive within
20 seconds per attempt. A 30-second subscription refresh confirms unchanged
fields and rediscovers optional objects while degraded. An explicit RPC
subscription rejection is a capability failure, not a transport failure. Rules require valid subscription and field observations within
120 seconds. The continuous threshold timer uses local monotonic time and
resets across outage/restart; a fired threshold remains latched until a
valid normal value. Missing optional objects/invalid configured fields appear
in subscription health and generate monitor_degraded / monitor_recovered.
Recovery does not imply that lost events were replayed.

Native notifications are opt-in. Different consecutive payloads are not
suppressed; identical consecutive payloads within 2 seconds are deduplicated
(button edges and G-code responses exempt). Informational G-code messages
are limited to one notification attempt per route per 10 seconds; their
records remain in SQLite, and warnings bypass that limit. This is best-effort,
not exactly-once delivery.
Oversize payloads above the conservative 16 KiB cap are counted and dropped.
Service state, CPU throttle bits, G-code responses and job/queue fields are
exposed when present; original positional params remain in event.params.
See [Moonraker JSON-RPC notifications](https://moonraker.readthedocs.io/en/latest/external_api/jsonrpc_notifications/).

## Configuration and schema

Unknown events and non-forwardable events configured on are errors.
Deprecated nozzle_force_anomaly: off warns; remove it. Message references
must be registered events or custom rules. Six source categories remain;
external ingestion is reserved. Registry definitions expose severity,
default routing, schema version, payload family and deduplication policy.
Dynamic severity can differ for native state transitions.

The v1 envelope is documented in ../event-envelope-v1.schema.json. Legacy
occurred_at remains local queue time; queued_at is UTC, received_at is
Monitor observation time (null for legacy rows), source_occurred_at is null
unless known (current adapters do not infer source wall-clock time).
Route delivery timestamps remain in SQLite. Agent acceptance is not
inference completion. Existing outbox IDs and original filament payload
remain compatible. Disconnect/recovery incident identity is persisted with
the alert. Periodic report sequences survive interval changes.

## Verification

```bash
python -m pytest -q
ruff check .
kalico-monitor config --check
kalico-monitor events --info monitor_degraded
kalico-monitor events --info notify_service_state_changed
```

On-device validation must cover actual Moonraker subscription refresh, optional
objects, Hermes/Feishu delivery, DB growth, and systemd restart. /kalico health
reports worker heartbeats and subscription health. Monitoring is not a
substitute for the printer's local safety protection.
