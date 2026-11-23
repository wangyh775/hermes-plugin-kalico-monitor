# Hermes Kalico Monitor

**Realtime, read-only Kalico/Klipper monitoring and Hermes notifications.**

[中文说明](README.zh-CN.md) · [Chinese deployment guide](docs/INSTALL.zh-CN.md) · [Configuration](docs/CONFIGURATION.zh-CN.md) · [Events](docs/EVENTS.zh-CN.md) · [Troubleshooting](docs/TROUBLESHOOTING.zh-CN.md)

**Status:** Experimental. The plugin manifest currently says v0.3.0 while Python packaging still says 0.2.0; no real printer-to-Hermes-to-Feishu end-to-end validation has been completed. This is not a safety system.

## Architecture

```text
Printer host                                  AI host (RK3588 / Jetson / Linux PC)
Kalico + Moonraker -- LAN HTTP/WebSocket --> Kalico Monitor (systemd + SQLite)
                                                   |
                                                   v
                                            Hermes Gateway -> Feishu
                                            Hermes plugin -> read-only Moonraker HTTP
```

The printer and AI host may be separate machines on a mutually reachable LAN; they need not share a subnet. For simpler deployment, run the Monitor and Hermes Gateway on the same host so the Hermes Webhook stays on localhost. **The monitor never sends G-code, movement, heating, pause, or resume commands.** Klippy remains responsible for real-time control and any optional local force-based pause/Z correction.

## Implemented features

- Moonraker WebSocket status subscription; print completion, pause, cancellation, errors, Klippy shutdown/disconnect/recovery, and Moonraker outage/recovery.
- SQLite outbox with stable alert IDs, retries, and deduplication; signed Hermes V2 Webhook with `deliver_only: true` for normal zero-LLM notifications.
- Optional print-progress reports (`KALICO_PROGRESS_REPORT_MINUTES=30`; default 0/off).
- Optional stall detection (`KALICO_STALL_MINUTES`; default 0/off).
- Optional custom Kalico [PR #13](https://github.com/wangyh775/kalico/pull/13) nozzle-force anomaly bridge (`KALICO_WATCH_NOZZLE_FORCE=1`; default off).
- Authorized zero-LLM `/kalico status`, `temp`, `force`, `alerts`, `health` and `help` slash commands, plus three read-only Agent tools.

**Not implemented:** Generic per-event YAML rules, arbitrary event routing, multi-printer aggregation, remote printer control, and individual alerts for successful first-layer Z adjustments. The latest Kalico PR #13 supports first-layer Z adjustment, but this bridge currently alerts primarily when its `trigger_count` changes on a pause request.

## Quick start

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
bash scripts/setup-user-service.sh
nano ~/.config/kalico-monitor/monitor.env
```

Set `MOONRAKER_URL`, `HERMES_WEBHOOK_SECRET`, `KALICO_MONITOR_DB`, and `KALICO_ALLOWED_CHAT_IDS`. Merge [the Hermes Webhook example](docs/hermes-webhook.example.yaml) into `~/.hermes/config.yaml`; do not overwrite existing platform configuration. The webhook route secret must match.

```bash
hermes plugins install "file://$PWD" --enable
systemctl --user enable --now kalico-monitor
journalctl --user -u kalico-monitor -f
```

The setup script **does not start** the daemon or modify Hermes settings. Installing the plugin does not start the independent Monitor service. See the [detailed Chinese installation guide](docs/INSTALL.zh-CN.md) for same-host and LAN-separated deployments, permissions, validation, and systemd variants.

## Testing and safety

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Tests are simulated; real Moonraker, Hermes Gateway and Feishu integration must still be validated. Independent thermal cutoffs, E-stop and power isolation are mandatory for high-temperature printers. See [NOTICE.md](NOTICE.md) for attribution.
