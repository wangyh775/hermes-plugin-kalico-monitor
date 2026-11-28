# Stage D fault injection

Baseline 9b2ae26. Test-only change. Strict expected failures (xfail) are open defects, not passing acceptance tests.

- Transaction abort during cursor checkpoint: verify gap, source event and cursor roll back together and retry succeeds.
- Database error in dispatch: fail supervisor rather than leave silent dead worker.
- Remote ACK followed by local checkpoint failure: reopen database, replay the same request ID; at-least-once behavior.
- D-M01: malformed ACK with non-string status must not kill the worker (strict xfail).
- D-M02: severity scheduling must not invert causally related incident events (strict xfail).

No physical printer or external webhook is used. Shared synchronous SQLite and absent permanent failure/dead-letter policy still require design review. Producer journal is bounded; cursor cannot recover evicted events.
