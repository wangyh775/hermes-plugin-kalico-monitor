#!/usr/bin/env bash
# One-command Linux bootstrap for an existing, configured Hermes Gateway.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer currently supports Linux (systemd user services) only." >&2
  exit 2
fi
if ! command -v python3 >/dev/null || ! command -v hermes >/dev/null; then
  echo "python3 and an initialized Hermes CLI are required." >&2
  exit 2
fi
if ! command -v systemctl >/dev/null; then
  echo "systemd user services are required." >&2
  exit 2
fi
# No sudo: installation is scoped to the invoking user's home.
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"
exec "$ROOT/.venv/bin/python" -m kalico_monitor.bootstrap "$@"
