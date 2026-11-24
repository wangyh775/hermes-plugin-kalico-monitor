#!/usr/bin/env bash
# One-command Linux bootstrap for an existing, configured Hermes Gateway.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer currently supports Linux (systemd user services) only." >&2
  exit 2
fi
if ! command -v python3 >/dev/null || ! command -v systemctl >/dev/null; then
  echo "python3 and systemd user services are required." >&2
  exit 2
fi

# Standalone maintenance works even if Hermes is not installed or running.
# Never require Hermes or change Hermes settings to manage Monitor autostart.
if [[ "$#" -eq 1 && "$1" == "--enable-autostart" ]]; then
  exec env PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m kalico_monitor.autostart
fi
if [[ "$#" -eq 1 && "$1" == "--check-autostart" ]]; then
  exec env PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m kalico_monitor.autostart --check
fi

if ! command -v hermes >/dev/null; then
  echo "An initialized Hermes CLI is required for the plugin installer." >&2
  exit 2
fi

# Do not install the plugin as root. Only logind's enable-linger step may
# ask for sudo; it does not elevate Python, Hermes, or Monitor itself.
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"
# bootstrap calls the autostart setup only after an explicit successful install.
exec "$ROOT/.venv/bin/python" -m kalico_monitor.bootstrap "$@"
