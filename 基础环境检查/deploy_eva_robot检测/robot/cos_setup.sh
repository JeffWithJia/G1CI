#!/usr/bin/env bash
set -euo pipefail

ROS_SETUP="/opt/ros/foxy/setup.bash"
# Use TUNA mirror as primary for China; fall back to GitHub
ROS_APT_KEY_URL="${ROS_APT_KEY_URL:-https://mirrors.tuna.tsinghua.edu.cn/rosdistro/ros.key}"
ROS_APT_KEY_FALLBACK_URL="${ROS_APT_KEY_FALLBACK_URL:-https://raw.githubusercontent.com/ros/rosdistro/master/ros.key}"
REALSENSE_APT_KEY_URL="${REALSENSE_APT_KEY_URL:-https://librealsense.realsenseai.com/Debian/librealsenseai.asc}"
REALSENSE_APT_REPO_URL="${REALSENSE_APT_REPO_URL:-https://librealsense.realsenseai.com/Debian/apt-repo}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-tv}"
MINICONDA_DIR="${MINICONDA_DIR:-$HOME/miniconda3}"

# China mirror configuration
PYPI_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
CONDA_MIRROR_BASE="https://mirrors.tuna.tsinghua.edu.cn/anaconda"
MINICONDA_MIRROR="${CONDA_MIRROR_BASE}/miniconda"
ROS_APT_MIRROR="${ROS_APT_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu}"
INSPIRE_SDK_DIR="${INSPIRE_SDK_DIR:-$HOME/inspire_hand_sdk}"
UNITREE_SDK2_PYTHON_DIR="${UNITREE_SDK2_PYTHON_DIR:-$HOME/unitree_sdk2_python}"
UNITREE_ROS2_DIR="${UNITREE_ROS2_DIR:-$HOME/unitree_ros2}"
CYCLONEDDS_HOME_DIR="${CYCLONEDDS_HOME_DIR:-$UNITREE_ROS2_DIR/cyclonedds_ws/install/cyclonedds}"
COS_WS_DIR="${COS_WS_DIR:-$HOME/cos_ws}"
APK_DIR="${APK_DIR:-$HOME/apk}"
TELEOP_TARGET_DIR="$COS_WS_DIR/src/teleop_server"
G1_DESCRIPTION_DIR="$COS_WS_DIR/src/g1_description"

log() {
  printf '\n[cos_setup.sh] %s\n' "$*"
}

die() {
  printf '\n[cos_setup.sh] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

require_dir() {
  [ -d "$1" ] || die "Required directory is missing: $1"
}

run_without_conda() {
  env -i \
    HOME="$HOME" \
    USER="${USER:-}" \
    LOGNAME="${LOGNAME:-${USER:-}}" \
    SHELL="${SHELL:-/bin/bash}" \
    TERM="${TERM:-dumb}" \
    LANG="${LANG:-C.UTF-8}" \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    bash --noprofile --norc -c "$*"
}

sudo_apt_update() {
  sudo apt-get update
}

download_file() {
  local url="$1"
  local dst="$2"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$dst"
    return
  fi

  if command -v wget >/dev/null 2>&1; then
    wget -qO "$dst" "$url"
    return
  fi

  die "Missing curl or wget, cannot download $url"
}

refresh_ros_apt_key() {
  local key_tmp keyring legacy_keyring trusted_keyring
  key_tmp="/tmp/ros-archive-keyring.gpg"
  keyring="/usr/share/keyrings/ros-archive-keyring.gpg"
  legacy_keyring="/usr/share/keyrings/ros2-latest-archive-keyring.gpg"
  trusted_keyring="/etc/apt/trusted.gpg.d/ros-archive-keyring.gpg"

  log "Refreshing ROS apt key"
  if ! download_file "$ROS_APT_KEY_URL" "$key_tmp"; then
    log "Failed to download ROS apt key from $ROS_APT_KEY_URL, trying $ROS_APT_KEY_FALLBACK_URL"
    download_file "$ROS_APT_KEY_FALLBACK_URL" "$key_tmp"
  fi
  sudo install -d -m 0755 /usr/share/keyrings
  sudo install -d -m 0755 /etc/apt/trusted.gpg.d
  sudo install -m 0644 "$key_tmp" "$keyring"
  sudo install -m 0644 "$key_tmp" "$legacy_keyring"
  sudo install -m 0644 "$key_tmp" "$trusted_keyring"
}

install_apt_packages() {
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
}

check_user_and_os() {
  if [ "$(id -u)" -eq 0 ]; then
    die "Run this script as the target user, not root. It uses \$HOME for install paths."
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
    die "ROS 2 Foxy is not installed at $ROS_SETUP. Install ROS 2 Foxy first, then rerun this script."
  fi
}

detect_deb_arch() {
  case "$(dpkg --print-architecture)" in
    amd64) printf 'amd64' ;;
    arm64) printf 'arm64' ;;
    *) die "Unsupported Debian architecture: $(dpkg --print-architecture). Expected amd64 or arm64." ;;
  esac
}

detect_miniconda_arch() {
  case "$(uname -m)" in
    x86_64) printf 'x86_64' ;;
    aarch64|arm64) printf 'aarch64' ;;
    *) die "Unsupported CPU architecture for Miniconda: $(uname -m)." ;;
  esac
}

install_realsense_repo() {
  local candidate codename key_tmp keyring source_list
  candidate="$(apt-cache policy librealsense2-dev 2>/dev/null | awk '/Candidate:/ {print $2; exit}')"
  if [ -n "$candidate" ] && [ "$candidate" != "(none)" ]; then
    return
  fi

  log "Adding Intel RealSense apt repository"
  key_tmp="/tmp/librealsenseai.asc"
  keyring="/etc/apt/keyrings/librealsenseai.gpg"
  source_list="/etc/apt/sources.list.d/librealsense.list"
  codename="$(lsb_release -cs)"

  sudo install -d -m 0755 /etc/apt/keyrings
  download_file "$REALSENSE_APT_KEY_URL" "$key_tmp"
  gpg --dearmor <"$key_tmp" | sudo tee "$keyring" >/dev/null
  sudo chmod 0644 "$keyring"

  echo "deb [signed-by=$keyring] $REALSENSE_APT_REPO_URL $codename main" |
    sudo tee "$source_list" >/dev/null
  sudo rm -f /etc/apt/sources.list.d/realsense-public.list
}

remove_realsense_repo_before_refresh() {
  sudo rm -f \
    /etc/apt/sources.list.d/librealsense.list \
    /etc/apt/sources.list.d/realsense-public.list
}

install_agent_deb() {
  if dpkg -s ros-foxy-agent >/dev/null 2>&1; then
    log "ros-foxy-agent is already installed"
    return
  fi

  local arch url deb
  arch="$(detect_deb_arch)"
  url="https://download.coscene.cn/agent/ros-foxy-agent_0.4.10-0focal_${arch}.deb"
  deb="/tmp/ros-foxy-agent_0.4.10-0focal_${arch}.deb"

  log "Installing ros-foxy-agent from $url"
  curl -fL "$url" -o "$deb"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$deb"
}

install_system_dependencies() {
  log "Installing apt dependencies"
  refresh_ros_apt_key
  configure_ros_apt_mirror
  remove_realsense_repo_before_refresh
  sudo_apt_update
  install_apt_packages \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    rsync \
    build-essential \
    cmake \
    pkg-config \
    tmux \
    python3-colcon-common-extensions \
    ros-foxy-rosidl-generator-dds-idl \
    ros-foxy-rosbag2-cpp \
    ros-foxy-cv-bridge \
    ros-foxy-robot-state-publisher \
    libeigen3-dev \
    libyaml-cpp-dev \
    libzmq3-dev \
    libopencv-dev

  install_realsense_repo
  sudo_apt_update
  install_apt_packages librealsense2-dev librealsense2-utils
  install_agent_deb
}

install_local_deb_packages() {
  require_dir "$APK_DIR"

  local debs=()
  local deb deb_arch host_arch
  host_arch="$(dpkg --print-architecture)"

  while IFS= read -r -d '' deb; do
    debs+=("$deb")
  done < <(find "$APK_DIR" -maxdepth 1 -type f -name '*.deb' -print0 | sort -z)

  if [ "${#debs[@]}" -eq 0 ]; then
    die "No .deb packages found in $APK_DIR"
  fi

  log "Installing local deb packages from $APK_DIR"
  for deb in "${debs[@]}"; do
    deb_arch="$(dpkg-deb -f "$deb" Architecture)"
    if [ "$deb_arch" != "all" ] && [ "$deb_arch" != "$host_arch" ]; then
      die "Debian package architecture mismatch: $deb is $deb_arch, host is $host_arch"
    fi

    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$deb"
  done
}

find_conda_setup() {
  local conda_base
  if command -v conda >/dev/null 2>&1; then
    conda_base="$(conda info --base)"
    if [ -f "$conda_base/etc/profile.d/conda.sh" ]; then
      printf '%s\n' "$conda_base/etc/profile.d/conda.sh"
      return
    fi
  fi

  if [ -f "$MINICONDA_DIR/etc/profile.d/conda.sh" ]; then
    printf '%s\n' "$MINICONDA_DIR/etc/profile.d/conda.sh"
    return
  fi

  if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then
    printf '%s\n' "$HOME/miniforge3/etc/profile.d/conda.sh"
    return
  fi

  return 1
}

install_miniconda_if_missing() {
  if find_conda_setup >/dev/null 2>&1; then
    log "Conda is already installed"
    return
  fi

  local arch installer
  arch="$(detect_miniconda_arch)"
  installer="/tmp/Miniconda3-latest-Linux-${arch}.sh"

  if [ -e "$MINICONDA_DIR" ]; then
    die "$MINICONDA_DIR exists but does not contain conda.sh. Set MINICONDA_DIR or fix that installation."
  fi

  log "Installing Miniconda to $MINICONDA_DIR (via TUNA mirror)"
  curl -fL "${MINICONDA_MIRROR}/Miniconda3-latest-Linux-${arch}.sh" -o "$installer"
  bash "$installer" -b -p "$MINICONDA_DIR"
}

configure_conda_china_mirrors() {
  log "Configuring conda to use TUNA mirrors (China)"
  cat > "$HOME/.condarc" <<EOF
channels:
  - defaults
show_channel_urls: true
default_channels:
  - ${CONDA_MIRROR_BASE}/pkgs/main
  - ${CONDA_MIRROR_BASE}/pkgs/r
  - ${CONDA_MIRROR_BASE}/pkgs/msys2
custom_channels:
  conda-forge: ${CONDA_MIRROR_BASE}/cloud
EOF
}

configure_ros_apt_mirror() {
  local ros_source_list codename
  ros_source_list="/etc/apt/sources.list.d/ros2.list"
  codename="$(lsb_release -cs)"

  log "Configuring ROS apt source to use TUNA mirror"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] ${ROS_APT_MIRROR} ${codename} main" \
    | sudo tee "$ros_source_list" >/dev/null
}

create_conda_env() {
  local conda_setup
  conda_setup="$(find_conda_setup)" || die "Conda setup script was not found after Miniconda installation."

  log "Creating or reusing conda env: $CONDA_ENV_NAME"
  # shellcheck disable=SC1090
  source "$conda_setup"
  configure_conda_china_mirrors
  if ! conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV_NAME"; then
    conda create -y -n "$CONDA_ENV_NAME" python=3.8 pip
  fi

  log "Installing Python SDKs into $CONDA_ENV_NAME"
  require_dir "$INSPIRE_SDK_DIR"
  require_dir "$UNITREE_SDK2_PYTHON_DIR"
  require_dir "$CYCLONEDDS_HOME_DIR"
  conda run -n "$CONDA_ENV_NAME" python -m pip install --upgrade pip -i "$PYPI_MIRROR"
  CYCLONEDDS_HOME="$CYCLONEDDS_HOME_DIR" \
    conda run -n "$CONDA_ENV_NAME" python -m pip install -e "$UNITREE_SDK2_PYTHON_DIR" -i "$PYPI_MIRROR"
  conda run -n "$CONDA_ENV_NAME" python -m pip install numpy pymodbus==3.6.9 pyserial -i "$PYPI_MIRROR"
  CYCLONEDDS_HOME="$CYCLONEDDS_HOME_DIR" \
    conda run -n "$CONDA_ENV_NAME" python -m pip install --no-deps -e "$INSPIRE_SDK_DIR" -i "$PYPI_MIRROR"
}

build_unitree_ros2() {
  log "Building Unitree ROS2 workspaces outside conda"
  require_dir "$UNITREE_ROS2_DIR"
  run_without_conda "
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
  log "Building teleop_server in $COS_WS_DIR outside conda"
  run_without_conda "
    set -eo pipefail
    cd '$COS_WS_DIR'
    source '$ROS_SETUP'
    source '$UNITREE_ROS2_DIR/cyclonedds_ws/install/setup.bash'
    colcon build --packages-select teleop_server
  "
}

install_systemd_services() {
  local install_script
  install_script="$HOME/scripts/install_service.sh"

  if [ ! -f "$install_script" ]; then
    die "Service install script not found: $install_script"
  fi

  log "Installing and enabling systemd services (cos_agent, xrobotoolkit-pc-service, cos_teleop)"
  sudo bash "$install_script"
}

print_next_steps() {
  log "Setup completed"
  cat <<EOF

Conda env:
  conda activate $CONDA_ENV_NAME

Unitree ROS2:
  source $UNITREE_ROS2_DIR/setup.sh

Python SDK build env:
  export CYCLONEDDS_HOME=$CYCLONEDDS_HOME_DIR

teleop_server:
  source $ROS_SETUP
  source $UNITREE_ROS2_DIR/cyclonedds_ws/install/setup.bash
  source $COS_WS_DIR/install/setup.bash

Systemd services:
  sudo systemctl start cos_agent xrobotoolkit-pc-service cos_teleop
  sudo systemctl status cos_agent xrobotoolkit-pc-service cos_teleop

Deployed source paths:
  $INSPIRE_SDK_DIR
  $UNITREE_SDK2_PYTHON_DIR
  $UNITREE_ROS2_DIR
  $TELEOP_TARGET_DIR
  $G1_DESCRIPTION_DIR
EOF
}

main() {
  require_command sudo
  require_command dpkg
  check_user_and_os
  install_system_dependencies
  install_local_deb_packages
  install_miniconda_if_missing
  build_unitree_ros2
  create_conda_env
  ensure_teleop_sources
  build_teleop_server
  install_systemd_services
  print_next_steps
}

main "$@"
