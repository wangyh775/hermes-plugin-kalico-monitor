#!/usr/bin/env bash
# Prepare a single-repository installation without automatically starting a daemon.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$HOME/.config/kalico-monitor"
UNIT_DIR="$HOME/.config/systemd/user"
ENV_FILE="$ENV_DIR/monitor.env"
UNIT_FILE="$UNIT_DIR/kalico-monitor.service"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required (systemd user service)" >&2
  exit 1
fi
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT"
mkdir -p "$ENV_DIR" "$UNIT_DIR"
if [[ ! -e "$ENV_FILE" ]]; then
  sed "s|/home/YOUR_USER|$HOME|g" "$ROOT/deploy/kalico-monitor.env.example" > "$ENV_FILE"
  echo "Created $ENV_FILE; you MUST configure a real Webhook secret before running."
fi
chmod 600 "$ENV_FILE"
cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=Kalico realtime printer alert monitor
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python -m kalico_monitor.monitor
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
printf '\nConfigured %s\n' "$UNIT_FILE"
printf '1. Run interactive setup: %s/.venv/bin/kalico-monitor config\n' "$ROOT"
printf '   Or edit manually: %s\n' "$ENV_FILE"
printf '2. Configure Hermes Webhook route and install the Hermes plugin from file://%s\n' "$ROOT"
printf '3. Then run: systemctl --user enable --now kalico-monitor\n'
printf '4. Inspect: systemctl --user status kalico-monitor\n'
printf 'NOTE: Persistent user services after logout may need loginctl enable-linger.\n'
