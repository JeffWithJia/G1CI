#!/usr/bin/env bash
# Install robot-side systemd services and enable them on boot.
# Usage: sudo bash install_service.sh
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Error: this script must be run as root (use sudo)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_SRC="${SCRIPT_DIR}/../systemd"
SERVICES=(
  cos_agent
  xrobotoolkit-pc-service
  cos_teleop
)

for svc in "${SERVICES[@]}"; do
  src="${SYSTEMD_SRC}/${svc}.service"
  if [[ ! -f "$src" ]]; then
    echo "Error: service file not found: $src" >&2
    exit 1
  fi

  cp "$src" "/etc/systemd/system/${svc}.service"
  echo "Installed -> /etc/systemd/system/${svc}.service"
done

systemctl daemon-reload

for svc in "${SERVICES[@]}"; do
  systemctl enable "${svc}.service"
  echo "Enabled ${svc}.service"
done

echo ""
echo "Start services with:"
echo "  sudo systemctl start ${SERVICES[*]}"
