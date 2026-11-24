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
# No sudo for plugin or Monitor installation. Only logind enable-linger may
# require privilege escalation, with an explicit prompt and clear scope.
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"

for arg in "$@"; do
  if [[ "$arg" == "--enable-autostart" ]]; then
    exec "$ROOT/.venv/bin/python" -m kalico_monitor.autostart
  fi
  if [[ "$arg" == "--check-autostart" ]]; then
    exec "$ROOT/.venv/bin/python" -m kalico_monitor.autostart --check
  fi
done

# Bootstrap remains in charge of Hermes configuration. Autostart changes ONLY
# the invoking user's logind/systemd configuration, not Hermes.
"$ROOT/.venv/bin/python" -m kalico_monitor.bootstrap "$@"

# --no-service is the explicit opt-out for all Monitor systemd management.
# Do not ask for linger in that mode.
for arg in "$@"; do
  if [[ "$arg" == "--no-service" ]]; then
    echo "Skipped Kalico Monitor boot autostart (--no-service)."
    exit 0
  fi
done

# If the user canceled bootstrap, there may be no active Monitor service.
# In that case never enable Linger for the entire user account.
if ! systemctl --user is-enabled --quiet kalico-monitor ||
   ! systemctl --user is-active --quiet kalico-monitor; then
  echo "Skipped unattended autostart: Kalico Monitor service is not enabled and active." >&2
  exit 0
fi

"$ROOT/.venv/bin/python" -m kalico_monitor.autostart
