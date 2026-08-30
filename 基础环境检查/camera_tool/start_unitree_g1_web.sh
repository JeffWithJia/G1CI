#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
HOST=${UNITREE_G1_WEB_HOST:-127.0.0.1}
PORT=${UNITREE_G1_WEB_PORT:-8765}

exec python3 "$ROOT/unitree_g1_web.py" --host "$HOST" --port "$PORT" "$@"
