# Reliability phase 2

Stacked on PR #26. No new event processing, Skills or printer control.
Shutdown notifications are persisted before diagnostic queries; details are
updated only before route assignment, otherwise retained locally. Shutdown
recovery has a separate incident key; route dispatch preserves causal order
within an incident. Local malformed payloads go to dead_letters; remote
delivery failures remain retryable. Query IDs are scoped to the connection.

An application-level server.info probe checks otherwise quiet sockets; a
watchdog checks transport loss independently of reconnect attempts. Bounded
connect timeouts reduce detection latency.
Threshold rules require a new observation to complete a duration window;
the subscription refresh provides confirmation of unchanged values.
History identity is best-effort (optional Moonraker history component),
with duration/position rollback as fallback. No guarantee of recovering all
jobs completed while disconnected.

Filament journals consume all supplied records, checkpoint cursor and event
together, and emit gaps for known boots. First observed sequence establishes
a baseline; gaps before monitoring began cannot be proved. The original
source event ID is retained. Source monotonic eventtime and connection/ready
epoch identifiers are distinct from UTC generation, observation and queue
times. Source wall time is not invented.

SQLite partial indexes and bounded compaction of terminal informational
G-code payloads retain event IDs and route audit. Compaction runs every minute,
only on records older than seven days, up to 1,000 per pass. It is not an
absolute database capacity cap; SQLite may reuse pages without shrinking
the file. Health displays DB size, dead letters, per-route queue and webhook status;
warns at 256 MiB database size or less than 128 MiB disk free.
No critical or pending records are removed. Test with real device and
long-duration high-rate traffic before production use.

Run python -m pytest -q and ruff check .; the pressure test exercises 5,000
terminal records and checks integrity and critical priority, not Jetson
throughput guarantees.
