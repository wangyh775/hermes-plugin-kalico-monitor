# Testing and acceptance

English | [简体中文](TESTING.zh-CN.md) · [Home](../README.md)

> Reference: main commit 0c47b84 (PR #21 merged). Read-only monitor; live printer–Hermes–Feishu acceptance outstanding.

## Automated

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
ruff check .
```

CI uses Python 3.10/3.12 and Ruff. Tests simulate lifecycle transitions, optional subscription fallback, route policy, outbox migration, event replay across restart, payload forwarding, configuration and setup. They do not prove live delivery.

## Acceptance checklist

- Verify read-only HTTP and WS and service heartbeat; check login-free startup.
- Real authorized chat: status/temp/alerts/health; test direct and agent route separately. Confirm no LLM for direct; accepted is not final delivery.
- Safe supervised producer event; compare source ID and samples with SQLite and webhook event field. Verify generic paused remains separate.
- Reconnect within producer retention; no duplicate source event. Test a longer disconnect separately and document possible loss.
- Verify off suppresses pending disallowed routes; both has independent retry state. Protect secrets and back up DB.
- Confirm Monitor does not issue G-code; printer remains protected when network/Monitor/Hermes is down.

Record versions, endpoint/route with credentials redacted, timestamps, IDs and actual outcomes. Real printer/Hermes/Feishu acceptance not yet recorded.
