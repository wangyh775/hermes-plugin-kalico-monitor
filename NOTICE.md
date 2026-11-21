# Upstream attribution

This implementation is a new, reduced-scope Hermes plugin and Moonraker monitor,
**not an automatic fork or exact copy** of the upstream Git repository.
It builds on the state comparison design of the MIT-licensed project:

- [TakeshiTGAL/hermes-plugin-klipper-print-watch](https://github.com/TakeshiTGAL/hermes-plugin-klipper-print-watch)
- Original author: TakeshiTGAL, 2026; original `watch.py` under MIT License.

The `kalico_monitor/watch.py` transition logic is adapted from the upstream
`watch.py`. Its own implementation is modified to track pause/resume without
resetting the generation, work with a durable outbox, and handle real-time events.
The original GitHub project also provides status querying, camera snapshots,
approval-gated remote controls, and Cron scheduling; those modules are **not**
included in this first version. This new plugin deliberately exposes only
read-only tools.
