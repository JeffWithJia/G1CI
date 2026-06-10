#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

REMOTE_USER="${REMOTE_USER:-unitree}"
REMOTE_HOST="${REMOTE_HOST:-}"

# Optional target argument support.
# Supported forms:
#   ./deploy_to_robot.sh 192.168.90.68
#   ./deploy_to_robot.sh unitree@192.168.90.68
#   ./deploy_to_robot.sh unitree@192.168.90.68:~
#   ./deploy_to_robot.sh unitree@192.168.90.68:/home/unitree
#
# Environment variables REMOTE_USER / REMOTE_HOST are still supported.
if [ "$#" -gt 1 ]; then
  printf '[deploy_to_robot] ERROR: Usage: %s [IP|user@IP|user@IP:~|user@IP:/home/unitree]\n' "$0" >&2
  exit 1
fi

TARGET="${1:-}"
if [ -n "$TARGET" ]; then
  # Strip optional scp-style remote path suffix, e.g. ":~" or ":/home/unitree".
  TARGET="${TARGET%%:*}"

  if [[ "$TARGET" == *@* ]]; then
    REMOTE_USER="${TARGET%@*}"
    REMOTE_HOST="${TARGET#*@}"
  else
    REMOTE_HOST="$TARGET"
  fi
fi

if [ -z "$REMOTE_HOST" ]; then
  printf '[deploy_to_robot] ERROR: REMOTE_HOST is required. Pass an IP/user@IP argument or set REMOTE_HOST.\n' >&2
  exit 1
fi

REMOTE="${REMOTE_USER}@${REMOTE_HOST}"

REMOTE_HOME="${REMOTE_HOME:-/home/unitree}"
REMOTE_WS_SRC="${REMOTE_WS_SRC:-/home/unitree/cos_ws/src}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-5}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-/tmp/cos_deploy_ssh_%r_%h_%p}"

SSH_OPTS=(
  -o "ConnectTimeout=$SSH_CONNECT_TIMEOUT"
  -o "ServerAliveInterval=5"
  -o "ServerAliveCountMax=2"
  -o "ControlMaster=auto"
  -o "ControlPersist=10m"
  -o "ControlPath=$SSH_CONTROL_PATH"
)
RSYNC_SSH="ssh ${SSH_OPTS[*]}"

SKIP_IF_EXISTS_DIRS=(
  "inspire_hand_sdk"
  "unitree_sdk2_python"
  "apk"
)

REPLACE_DIRS=(
  "unitree_ros2"
  "g1_description"
  "teleop_server"
  "scripts"
  "systemd"
)
REPLACE_PARENT_unitree_ros2="$REMOTE_HOME"
REPLACE_PARENT_scripts="$REMOTE_HOME"
REPLACE_PARENT_systemd="$REMOTE_HOME"

HOME_FILES=(
  "cos_setup.sh"
)

log() {
  printf '[deploy_to_robot] %s\n' "$*"
}

die() {
  printf '[deploy_to_robot] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

remote_quote() {
  printf "'%s'" "${1//\'/\'\\\'\'}"
}

ssh_run() {
  ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"
}

close_ssh_connection() {
  ssh "${SSH_OPTS[@]}" -O exit "$REMOTE" >/dev/null 2>&1 || true
}

check_ssh_connection() {
  log "Checking SSH connection to $REMOTE"
  ssh_run "true" || die "Cannot connect to $REMOTE over SSH. Check robot IP, network, SSH service, and credentials."
  trap close_ssh_connection EXIT
}

remote_dir_exists() {
  local remote_dir="$1"
  local quoted_dir status
  quoted_dir="$(remote_quote "$remote_dir")"

  set +e
  ssh_run "test -d $quoted_dir"
  status=$?
  set -e

  if [ "$status" -eq 0 ]; then
    return 0
  fi
  if [ "$status" -eq 1 ]; then
    return 1
  fi
  if [ "$status" -eq 255 ]; then
    die "SSH connection to $REMOTE failed while checking $remote_dir"
  fi
  die "Remote directory check failed for $remote_dir with exit code $status"
}

sync_dir() {
  local local_dir="$1"
  local remote_parent="$2"

  [ -d "$SCRIPT_DIR/$local_dir" ] || die "Local directory not found: $SCRIPT_DIR/$local_dir"
  rsync -az --delete \
    -e "$RSYNC_SSH" \
    --exclude ".git/" \
    --exclude ".idea/" \
    --exclude "cmake-build-debug/" \
    --exclude "build/" \
    --exclude "install/" \
    --exclude "log/" \
    --exclude "__pycache__/" \
    "$SCRIPT_DIR/$local_dir/" "$REMOTE:$remote_parent/$local_dir/"
}

copy_if_missing() {
  local dir="$1"
  local remote_dir="$REMOTE_HOME/$dir"

  if remote_dir_exists "$remote_dir"; then
    log "Skip existing $remote_dir"
    return
  fi

  log "Copy $dir -> $REMOTE:$remote_dir"
  ssh_run "mkdir -p $(remote_quote "$REMOTE_HOME")"
  sync_dir "$dir" "$REMOTE_HOME"
}

replace_dir() {
  local dir="$1"
  local remote_parent="$REMOTE_WS_SRC"
  local remote_parent_var="REPLACE_PARENT_${dir}"

  if [ -n "${!remote_parent_var:-}" ]; then
    remote_parent="${!remote_parent_var}"
  fi

  local remote_dir="$remote_parent/$dir"

  log "Replace $remote_dir"
  ssh_run "mkdir -p $(remote_quote "$remote_parent") && rm -rf $(remote_quote "$remote_dir")"
  sync_dir "$dir" "$remote_parent"
}

copy_home_file() {
  local file="$1"

  [ -f "$SCRIPT_DIR/$file" ] || die "Local file not found: $SCRIPT_DIR/$file"
  log "Copy $file -> $REMOTE:$REMOTE_HOME/$file"
  ssh_run "mkdir -p $(remote_quote "$REMOTE_HOME")"
  rsync -az -e "$RSYNC_SSH" "$SCRIPT_DIR/$file" "$REMOTE:$REMOTE_HOME/$file"
}

main() {
  require_command ssh
  require_command rsync

  log "Deploying robot bundle to $REMOTE"
  check_ssh_connection

  for dir in "${SKIP_IF_EXISTS_DIRS[@]}"; do
    copy_if_missing "$dir"
  done

  for dir in "${REPLACE_DIRS[@]}"; do
    replace_dir "$dir"
  done

  for file in "${HOME_FILES[@]}"; do
    copy_home_file "$file"
  done

  log "Done"
}

main "$@"
