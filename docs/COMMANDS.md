# Commands

English | [简体中文](COMMANDS.zh-CN.md) · [Home](../README.md)

> Reference: main commit 0c47b84 (PR #21 merged). Read-only monitor; live printer–Hermes–Feishu acceptance outstanding.

## CLI

Run from repository directory using `.venv/bin/kalico-monitor`. The command tool is read-only except config/apply and synthetic notification delivery.

| Command | Purpose |
|---|---|
| `config` | Interactive configuration |
| `config --show` | Masked settings |
| `config --check` | Validation |
| `config --test` | Connectivity |
| `config --apply` | Confirm and apply |
| `events` | Known routes |
| `events --watch` | Raw base Moonraker notifications; not all optional objects |
| `discover` | List status objects |
| `test filament_jam_detected` | Send synthetic notification under policy; may call LLM |
| `/kalico status` | Printer status and configurable fields |
| `/kalico temp` | Nozzle and bed |
| `/kalico force` | Legacy force monitor status |
| `/kalico alerts` | Recent alerts / pending count |
| `/kalico health` | Heartbeat and queue |
| `/kalico help` | Help |

Quick commands are authorized through Hermes, may have an additional chat allowlist and do not invoke LLM. No /kalico motion commands. `test` does not create a real printer fault or production outbox record.
