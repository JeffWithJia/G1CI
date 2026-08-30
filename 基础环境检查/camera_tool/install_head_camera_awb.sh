#!/usr/bin/env bash
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME=unitree-g1-head-camera-awb.service
SERVICE_SOURCE="$TOOL_DIR/$SERVICE_NAME"
SERVICE_TARGET="/etc/systemd/system/$SERVICE_NAME"
RESTORE_SCRIPT="$TOOL_DIR/restore_head_awb_after_teleop.sh"
STATE_FILE="$TOOL_DIR/camera-controls-state.json"

for path in "$SERVICE_SOURCE" "$RESTORE_SCRIPT" "$STATE_FILE" "$TOOL_DIR/unitree_g1_web.py"; do
  if [[ ! -f "$path" ]]; then
    printf 'missing required file: %s\n' "$path" >&2
    exit 1
  fi
done

chmod 755 "$RESTORE_SCRIPT"
chmod 600 "$STATE_FILE"
sudo install -o root -g root -m 0644 "$SERVICE_SOURCE" "$SERVICE_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

printf 'installed: %s\n' "$SERVICE_NAME"
printf 'one-click adjustment: sudo systemctl restart %s\n' "$SERVICE_NAME"
printf 'teleop was not modified or restarted.\n'
