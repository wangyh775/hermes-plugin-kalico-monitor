# Hermes Kalico Monitor

**Realtime, read-only Kalico/Klipper monitoring and Hermes notifications.**

[中文说明](README.zh-CN.md) · [Chinese deployment guide](docs/INSTALL.zh-CN.md) · [Configuration](docs/CONFIGURATION.zh-CN.md) · [Events](docs/EVENTS.zh-CN.md) · [Event routing](docs/EVENT_ROUTING.zh-CN.md) · [Troubleshooting](docs/TROUBLESHOOTING.zh-CN.md)

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
- Hermes-authorized zero-LLM `/kalico status`, `temp`, `force`, `alerts`, `health` and `help` slash commands, plus three read-only Agent tools.

- Per-event `direct`, `agent`, or `both` delivery via `KALICO_EVENT_MODES`, with an optional second Hermes Agent webhook. Durable independent retry records in SQLite and route-specific idempotency IDs; the default remains `direct`.
- Interactive `kalico-monitor config` wizard, including optional event policies in menu `8`, masked secrets, and read-only connectivity check.

**Not implemented:** Generic conditional per-event YAML rules, multi-printer aggregation, remote printer control, and individual alerts for successful first-layer Z adjustments. The latest Kalico PR #13 supports first-layer Z adjustment, but this bridge currently alerts primarily when its `trigger_count` changes on a pause request.

## Linux one-click installer

If Hermes is already configured with Feishu and an LLM, use the automated bootstrap:

```bash
bash install.sh
```

It prompts for the Moonraker URL, safely merges Hermes webhook routes without replacing existing Feishu/model settings, installs the Hermes plugin, manages the Monitor systemd user service, creates timestamped backups, and verifies the HTTP endpoints plus a harmless signed Feishu test notification. Default printer is `PEEK-01` and critical events use `both`. Full prerequisites, safeguards and limits: [Linux one-click guide](docs/ONE_CLICK_INSTALL.zh-CN.md). The feature is under review in PR #9 until merged.

## Manual quick start

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
bash scripts/setup-user-service.sh
.venv/bin/kalico-monitor config  # interactive setup (or edit monitor.env manually)
```

Set `MOONRAKER_URL`, `HERMES_WEBHOOK_SECRET`, and `KALICO_MONITOR_DB`. `KALICO_ALLOWED_CHAT_IDS` is optional because Hermes Gateway handles inbound authorization. For model-assisted events also set `HERMES_AGENT_WEBHOOK_URL` and `KALICO_EVENT_MODES`. Merge [the Hermes Webhook example](docs/hermes-webhook.example.yaml) into `~/.hermes/config.yaml`; do not overwrite existing platform configuration. The webhook route secret must match for each enabled route. `agent` / `both` events require a configured `kalico-analysis` route. See the [event routing guide](docs/EVENT_ROUTING.zh-CN.md).

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

## Upgrade notes

PR #7 (configuration wizard) and PR #8 (per-event Hermes routing) have been merged into `main`. Existing installs can update with `git pull`, reinstall using `.venv/bin/python -m pip install -e .`, then run `.venv/bin/kalico-monitor config` and restart the user service. The default remains zero-LLM direct delivery. GitHub CI passed on Python 3.10 and 3.12 with Ruff; real Gateway/Feishu end-to-end validation is still required. A `status=accepted` response acknowledges Agent task admission, not final analysis completion or Feishu delivery.
