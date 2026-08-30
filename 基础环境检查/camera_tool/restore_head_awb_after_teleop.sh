#!/usr/bin/env bash
set -euo pipefail

CAMERA_TOOL_DIR=/home/unitree/camera_tool
HEAD_DEVICE=/dev/cam_head
STREAM_SETTLE_SECONDS="${HEAD_CAMERA_STREAM_SETTLE_SECONDS:-5}"
AWB_WARM_SECONDS="${HEAD_CAMERA_AWB_WARM_SECONDS:-5}"

case "$STREAM_SETTLE_SECONDS:$AWB_WARM_SECONDS" in
  *[!0-9.:]*)
    printf '[head-camera-awb] invalid timing configuration: settle=%s warm=%s\n' \
      "$STREAM_SETTLE_SECONDS" "$AWB_WARM_SECONDS" >&2
    exit 2
    ;;
esac

log() {
  printf '[head-camera-awb] %s\n' "$*"
}

on_error() {
  status=$?
  log "failed with exit code ${status}"
  exit "$status"
}
trap on_error ERR

log "waiting ${STREAM_SETTLE_SECONDS}s for teleop camera stream"
sleep "$STREAM_SETTLE_SECONDS"

if [[ ! -e "$HEAD_DEVICE" ]]; then
  log "camera device is unavailable: ${HEAD_DEVICE}"
  exit 1
fi

cd "$CAMERA_TOOL_DIR"
log "warming head AWB for ${AWB_WARM_SECONDS}s, then applying saved head controls"
python3 - "$HEAD_DEVICE" "$AWB_WARM_SECONDS" <<'PY'
import sys

import unitree_g1_web as camera_web

device = sys.argv[1]
warm_seconds = float(sys.argv[2])
result = camera_web.restore_persistent_controls(device, warm_seconds=warm_seconds)
if result is None:
    raise RuntimeError(f"no saved camera controls found for {device}")
PY
log "head AWB initialization and saved-control restore completed"
