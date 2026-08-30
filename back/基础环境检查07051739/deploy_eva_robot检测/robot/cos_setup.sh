#!/usr/bin/env bash
set -Eeuo pipefail

ROS_SETUP="/opt/ros/foxy/setup.bash"

TARGET_HOME="${TARGET_HOME:-/home/unitree}"
UNITREE_ROS2_DIR="${UNITREE_ROS2_DIR:-$TARGET_HOME/unitree_ros2}"
COS_WS_DIR="${COS_WS_DIR:-$TARGET_HOME/cos_ws}"
TELEOP_TARGET_DIR="$COS_WS_DIR/src/teleop_server"
G1_DESCRIPTION_DIR="$COS_WS_DIR/src/g1_description"

log() {
  printf '\n[cos_setup.sh] %s\n' "$*"
}

error() {
  if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
    printf '\n\033[31m[cos_setup.sh] ERROR: %s\033[0m\n' "$*" >&2
  else
    printf '\n[cos_setup.sh] ERROR: %s\n' "$*" >&2
  fi
}

die() {
  error "$*"
  exit 1
}

on_error() {
  local exit_code=$?
  local line_no=${BASH_LINENO[0]:-unknown}
  local command=${BASH_COMMAND:-unknown}
  error "Script failed at line $line_no with exit code $exit_code: $command"
  exit "$exit_code"
}

trap on_error ERR

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

require_dir() {
  [ -d "$1" ] || die "Required directory is missing: $1"
}

run_clean_env() {
  env -i \
    HOME="$TARGET_HOME" \
    USER="${TARGET_USER:-unitree}" \
    LOGNAME="${TARGET_USER:-unitree}" \
    SHELL="${SHELL:-/bin/bash}" \
    TERM="${TERM:-dumb}" \
    LANG="${LANG:-C.UTF-8}" \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    bash --noprofile --norc -c "$*"
}

check_user_and_os() {
  if [ "$(id -u)" -eq 0 ]; then
    die "Run this script as the target user. Privileged setup is handled by Ansible become tasks."
  fi

  if [ ! -r /etc/os-release ]; then
    die "Cannot detect OS release."
  fi

  # shellcheck disable=SC1091
  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "20.04" ]; then
    die "This setup script is intended for Ubuntu 20.04. Detected ${PRETTY_NAME:-unknown}."
  fi

  if [ ! -f "$ROS_SETUP" ]; then
    die "ROS 2 Foxy is not installed at $ROS_SETUP. Ansible must install system dependencies before running this script."
  fi
}

build_unitree_ros2() {
  log "Building Unitree ROS2 workspaces"
  require_dir "$UNITREE_ROS2_DIR"
  run_clean_env "
    set -eo pipefail
    cd '$UNITREE_ROS2_DIR/cyclonedds_ws'
    export LD_LIBRARY_PATH=/opt/ros/foxy/lib
    colcon build --packages-select cyclonedds
    source '$ROS_SETUP'
    colcon build
  "
}

ensure_teleop_sources() {
  require_dir "$TELEOP_TARGET_DIR"
  require_dir "$G1_DESCRIPTION_DIR"
}

build_teleop_server() {
  log "Building teleop_server in $COS_WS_DIR"
  run_clean_env "
    set -eo pipefail
    cd '$COS_WS_DIR'
    source '$ROS_SETUP'
    source '$UNITREE_ROS2_DIR/cyclonedds_ws/install/setup.bash'
    colcon build --packages-select teleop_server
  "
}

print_next_steps() {
  log "Setup completed"
  cat <<EOF

teleop_server:
  source $ROS_SETUP
  source $UNITREE_ROS2_DIR/cyclonedds_ws/install/setup.bash
  source $COS_WS_DIR/install/setup.bash

Systemd services:
  systemctl start cos_agent xrobotoolkit-pc-service cos_teleop
  systemctl status cos_agent xrobotoolkit-pc-service cos_teleop
  journalctl -fu cos_teleop

Deployed source paths:
  $UNITREE_ROS2_DIR
  $TELEOP_TARGET_DIR
  $G1_DESCRIPTION_DIR
EOF
}

main() {
  require_command dpkg
  check_user_and_os
  build_unitree_ros2
  ensure_teleop_sources
  build_teleop_server
  print_next_steps
}

main "$@"
