# Hermes Kalico Monitor

Realtime, camera-free Kalico/Klipper event alerts through Moonraker WebSocket and the Hermes messaging gateway. Designed for an RK3588 running Kalico, Moonraker and Hermes. **Development prototype (v0.1.0); not field-validated or a safety system.**

中文说明：[README.zh-CN.md](README.zh-CN.md).

## Features

- Subscribes to `print_stats`, `webhooks` and `virtual_sdcard`; handles Moonraker `notify_status_update`, `notify_klippy_shutdown`, `notify_klippy_disconnected`, and G-code response events.
- Detects print complete, error, cancel, pause, MCU shutdown, Klippy lost/recovered, Moonraker connection lost/recovered; optional stall detection (disabled by default).
- Persists state and undelivered messages in SQLite; retries failed messages, uses deterministic alert IDs and Hermes `X-Request-ID` dedupe.
- Sends a signed Hermes `generic` V2 Webhook using `deliver_only: true`: no LLM tokens for normal alerts.
- Provides optional read-only `kalico_status` and `kalico_monitor_health` Hermes tools.
- Does **not** require Obico, camera, public IP, or control of printer movement. **Does not include the complete upstream plugin's control/camera/Cron tools.**

Architecture:

```text
Kalico -> Moonraker WebSocket -> kalico-monitor (systemd + SQLite outbox)
                                  -> Hermes signed Webhook (deliver_only)
                                  -> Feishu group / supported Hermes channel
```

## On RK3588: install the event monitor

These steps assume Linux, working Moonraker and Hermes Gateway on the same device:

```bash
git clone https://github.com/wangyh775/hermes-plugin-kalico-monitor.git
cd hermes-plugin-kalico-monitor
curl http://127.0.0.1:7125/printer/info
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

1. Copy `deploy/kalico-monitor.env.example` to `/home/YOUR_USER/.config/kalico-monitor/monitor.env`; replace `YOUR_USER`, select the printer label and generate a secret with `python3 -c 'import secrets; print(secrets.token_hex(32))'`.
2. Merge `docs/hermes-webhook.example.yaml` into `~/.hermes/config.yaml`, using the same secret and a connected Feishu group `chat_id`. Do not overwrite other existing `platforms` configuration. Verify the Hermes webhook route on your installed version; examples are not guaranteed portable.
3. With the environment loaded, run locally first:

```bash
set -a
. /home/YOUR_USER/.config/kalico-monitor/monitor.env
set +a
python -m kalico_monitor.monitor
```

4. To run continuously, copy and edit `deploy/kalico-monitor.service.example` to `/etc/systemd/system/kalico-monitor.service`. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kalico-monitor
sudo journalctl -u kalico-monitor -f
```

5. Optionally register the Hermes read-only plugin after installation (Hermes >=0.21.6):

```bash
hermes plugins install file:///home/YOUR_USER/hermes-plugin-kalico-monitor --enable
```

The realtime listener is a **separate systemd service**. Installing the Hermes plugin alone does not start monitoring. To avoid duplicate notifications, disable the upstream `klipper-print-watch` Cron schedule if it was previously enabled.

## Verified/expected interfaces

- `ws://127.0.0.1:7125/websocket` and JSON-RPC `printer.objects.subscribe` are Moonraker interfaces.
- `notify_klippy_shutdown` carries **no failure message** by itself; this implementation queries the state separately.
- Proposed Hermes generic signature: `HMAC_SHA256(secret, timestamp + '.' + raw_json_body)`, with `X-Webhook-Timestamp` and `X-Webhook-Signature-V2` headers; verify on the deployment Hermes version.
- `X-Request-ID` stays fixed across retries. HTTP success with `status=delivered` or `duplicate` counts as success; other responses retry.
- Delivery requires an existing and correctly connected Hermes messaging platform. Plain WeChat groups are not assured by the Weixin adapter; Feishu group is the recommended default.

## Tests

```bash
pip install -e '.[test]'
pytest -q
```

All bundled tests use simulated status/events and local files. No actual firmware shutdown or heater failure is induced. Real RK3588/Hermes/Feishu testing remains required.

## Reliability and safety limitations

- The first working subscription establishes a baseline; if state was already `shutdown` at startup, a critical alert is issued. Persisted state is reconciled after process restart.
- The monitor shares a host with the printer, so it cannot send alerts during total host power loss, host crash or network outage. An off-host heartbeat monitor is needed for those failures.
- Automatic alerting is **not** a certified safety mechanism. Independent thermal cut-off, power disconnect, E-stop and firmware protection remain necessary, particularly for PEEK printers.
- No G-code, heat command or safety threshold change is sent. Hermes tools provided here are read-only.

## Project origin

Based on the MIT-licensed state comparison ideas in [TakeshiTGAL/hermes-plugin-klipper-print-watch](https://github.com/TakeshiTGAL/hermes-plugin-klipper-print-watch). Attribution and differences: [NOTICE.md](NOTICE.md).

## Development roadmap

- v0.1: WebSocket, state classifier, SQLite outbox, Hermes direct delivery, simulated tests.
- v0.2: better error-context correlation, ack/escalation, detailed reliability metrics and diagnostics.
- v0.3: optional multi-printer config and external heartbeat integration.

## Optional periodic print progress delivery

Set `KALICO_PROGRESS_REPORT_MINUTES=30` in the Monitor environment file (0=disabled, 5..1440 permitted). Restart the systemd monitor service. While `print_stats.state=printing` and Klippy is ready with recently updated Moonraker status, the Monitor sends a direct, zero-LLM Hermes Webhook every 30 minutes with filename, estimated file progress, print elapsed time, nozzle/bed temperatures, and available layer info. The timer is persisted alongside the SQLite outbox; it pauses when printing pauses, the device shuts down, or the Moonraker connection goes stale. Delivery retries use the existing signed Webhook and stable per-report ID. Previously queued reports may be delivered after connectivity is restored and should not be mistaken for live status.

See [Chinese installation guide](docs/V0.2_INSTALL.zh-CN.md).

## Optional custom nozzle force anomaly alerts

Set `KALICO_WATCH_NOZZLE_FORCE=1` to subscribe to the custom Klippy PR #13 `nozzle_force_monitor` object. Alerts are based on `triggered` and `trigger_count` changes, use the existing SQLite/Hermes delivery queue, and never control the printer. If the object is absent, Monitor falls back to the original subscription. `/kalico force` is a read-only, zero-LLM Feishu command. See the [Chinese guide](docs/V0.2_INSTALL.zh-CN.md).
