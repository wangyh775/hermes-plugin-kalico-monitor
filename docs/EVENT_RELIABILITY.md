# Event reliability

Monitor uses one routing task and independent direct/agent delivery workers. Each reselects one ready route by severity (critical, warning, info), then creation time. An already in-flight request cannot be preempted, but Agent waiting does not block Direct. Unexpected worker return, cancellation or exception stops siblings and fails the daemon; systemd's restart policy is responsible for restart. SQLite outbox and route-specific request IDs remain unchanged. Agent success means admission, not final chat delivery. Retries are at-least-once, not exactly-once.

Optional filament_force journal ingestion atomically persists the event, gap alert and per-printer/per-boot high-water cursor. A first observed event establishes the observation boundary (no claim of earlier losses). Within that boot, a jump creates filament_event_gap with missing_from_seq, missing_to_seq, observed_seq, missing_count. Late replay is still deduplicated/accepted and does not lower the cursor. The gap records a possible loss at observation time, not proof of permanent loss; a new boot cannot prove what happened before shutdown. RAM retention is finite; there is no guaranteed replay of discarded events. Invalid/unknown events are skipped and counted in rate-limited logs. The new filament_sensor_fault carries original schema-1 watchdog payload; older producers remain compatible. recovery_failed remains a separate state transition, so both events may appear.

```ini
[events]
default: direct
filament_sensor_fault: direct
filament_event_gap: direct
filament_recovery_failed: both
```

Example only; configure and test Agent first. Monitor stays read-only.
