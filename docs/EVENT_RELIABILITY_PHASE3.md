# Reliability phase 3

Stacked on PR #28. No new event-processing, Skill, or printer-control behavior.
Critical events whose optional content cannot be serialized are quarantined
and a minimal event with the original ID is persisted. WriteResult distinguishes
inserted, duplicate, degraded, and quarantined. The public enqueue method still
returns the event ID for compatibility. Ready state and incident recovery are
committed together. Old incident keys are backfilled where the historical
incident ID can be established; unprovable relationships remain unlinked.

A transport epoch invalidates late responses after a socket has been declared
lost. Transport connection, RPC responsiveness and subscription availability
are reported independently. A responsive peer with an expired subscription
is degraded and resubscribed, rather than forcibly disconnected. HTTP 400,
413 and 422 are treated as terminal delivery rejections, retaining dead letters;
other remote errors remain retryable. Recovery messages warn that an earlier
fault may not have been delivered. Agent acknowledgement order is not inference
completion order. External error text is bounded and safely encoded.

Repeated journal snapshots are skipped; already persisted event IDs do not
require another write. Late previously missing events remain eligible. A new
job is not simultaneously a resumed job. SQLite maintenance runs separately
from liveness checks, with 100-record batches and a 250 ms busy timeout.
Synchronous disk I/O may still block the loop; there is no single-writer
thread or hard database capacity limit. A lock timeout remains a system-level
failure, not a successful commit.

Optional monitor.cfg:
```ini
[reliability]
subscription_refresh: 30
subscription_timeout: 20
observation_gap: 35
field_max_age: 120
connect_timeout: 5

[status]
chamber_object: temperature_sensor chamber
```
Observation gap must exceed refresh and not exceed max age. Explicit chamber
objects are discovered and subscribed when available. Health reports recent
Webhook outcomes, marks outcomes older than 300 seconds historical, and shows
oldest queued route age. Source time remains nullable and is never guessed.

Read-only commands:
```sh
kalico-monitor dead-letters list
kalico-monitor dead-letters show EVENT_ID
kalico-monitor incidents list
kalico-monitor storage status
```
No automatic dead-letter replay or deletion. Queries require the migrated DB
and normal configuration. Tests include transaction rollback, stable degraded
ID, incident migration, concurrent readers and bounded writer lock contention.
Run pytest and ruff before deployment; CI is not physical Jetson validation.
