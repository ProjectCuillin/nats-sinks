#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

# Unified systemd installer for nats-sinks.
#
# The script intentionally detects only the Linux families we document and
# test for service deployment: Debian-family systems and Oracle Linux.  It
# keeps Prometheus observability service assets installed but disabled by
# default, so no external metric sharing happens until an operator reviews and
# enables the observability policy and the selected textfile timer or native
# HTTP endpoint service.

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
NATS_SINKS_INSTALL_REF=${NATS_SINKS_INSTALL_REF:-main}
NATS_SINKS_REPO_RAW_BASE=${NATS_SINKS_REPO_RAW_BASE:-"https://raw.githubusercontent.com/ProjectCuillin/nats-sinks/$NATS_SINKS_INSTALL_REF"}
NATS_SINKS_PACKAGE_SPEC=${NATS_SINKS_PACKAGE_SPEC:-}
LOCAL_ASSETS_AVAILABLE=false

if [ -f "$REPO_ROOT/pyproject.toml" ] && grep -q 'name = "nats-sinks"' "$REPO_ROOT/pyproject.toml"; then
  LOCAL_ASSETS_AVAILABLE=true
fi

if [ -z "$NATS_SINKS_PACKAGE_SPEC" ]; then
  case "$NATS_SINKS_INSTALL_REF" in
    v[0-9]*)
      NATS_SINKS_PACKAGE_SPEC="nats-sinks==${NATS_SINKS_INSTALL_REF#v}"
      ;;
    *)
      NATS_SINKS_PACKAGE_SPEC="nats-sinks"
      ;;
  esac
fi

if [ ! -r /etc/os-release ]; then
  echo "Cannot detect operating system: /etc/os-release is missing or unreadable." >&2
  exit 2
fi

# /etc/os-release is the standard Linux OS identification file.  The file is
# owned by the local operating system and contains shell-style assignments.
# shellcheck disable=SC1091
. /etc/os-release

OS_ID=${ID:-}
OS_ID_LIKE=${ID_LIKE:-}
OS_MATCH=" $OS_ID $OS_ID_LIKE "

PACKAGE_MANAGER=""
SERVICE_SHELL=""

case "$OS_MATCH" in
  *" debian "* | *" ubuntu "*)
    PACKAGE_MANAGER="apt"
    SERVICE_SHELL="/usr/sbin/nologin"
    ;;
  *" ol "* | *" oracle "* | *" oraclelinux "*)
    PACKAGE_MANAGER="dnf"
    SERVICE_SHELL="/sbin/nologin"
    ;;
  *)
    echo "Unsupported Linux distribution for this installer: ID='$OS_ID' ID_LIKE='$OS_ID_LIKE'." >&2
    echo "Supported families are Debian and Oracle Linux." >&2
    exit 2
    ;;
esac

install_project_file() {
  relative_path=$1
  mode=$2
  owner=$3
  group=$4
  destination=$5
  local_path="$REPO_ROOT/$relative_path"

  if [ "$LOCAL_ASSETS_AVAILABLE" = "true" ] && [ -f "$local_path" ]; then
    install -m "$mode" -o "$owner" -g "$group" "$local_path" "$destination"
    return
  fi

  if ! command -v curl >/dev/null 2>&1; then
    echo "Cannot fetch $relative_path because curl is not installed." >&2
    exit 2
  fi

  temp_file=$(mktemp)
  if ! curl -fsSL "$NATS_SINKS_REPO_RAW_BASE/$relative_path" -o "$temp_file"; then
    rm -f "$temp_file"
    echo "Failed to download $relative_path from $NATS_SINKS_REPO_RAW_BASE." >&2
    exit 2
  fi
  install -m "$mode" -o "$owner" -g "$group" "$temp_file" "$destination"
  rm -f "$temp_file"
}

case "$PACKAGE_MANAGER" in
  apt)
    apt-get update
    apt-get install -y python3 python3-venv python3-pip curl
    ;;
  dnf)
    dnf install -y python3 python3-pip curl
    ;;
  *)
    echo "Internal installer error: unknown package manager '$PACKAGE_MANAGER'." >&2
    exit 2
    ;;
esac

if ! id nats-sink >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/nats-sink --create-home --shell "$SERVICE_SHELL" nats-sink
fi

install -d -o nats-sink -g nats-sink /var/lib/nats-sink
install -d -o nats-sink -g nats-sink /var/lib/node_exporter/textfile_collector
install -d /etc/nats-sinks
install -d /opt/nats-sinks

python3 -m venv /opt/nats-sinks/venv
/opt/nats-sinks/venv/bin/python -m pip install --upgrade pip
/opt/nats-sinks/venv/bin/python -m pip install "$NATS_SINKS_PACKAGE_SPEC"

if [ ! -f /etc/nats-sinks/config.json ]; then
  install_project_file examples/file-basic/config.json 0640 root nats-sink /etc/nats-sinks/config.json
fi

if [ ! -f /etc/nats-sinks/nats-sink.env ]; then
  install_project_file examples/systemd/nats-sink.env 0640 root nats-sink /etc/nats-sinks/nats-sink.env
fi

if [ ! -f /etc/nats-sinks/observability.prometheus.json ]; then
  install_project_file examples/systemd/observability.prometheus.json 0640 root nats-sink /etc/nats-sinks/observability.prometheus.json
fi

install_project_file examples/systemd/nats-sink.service 0644 root root /etc/systemd/system/nats-sink.service
install_project_file examples/systemd/nats-sink-prometheus-textfile.service 0644 root root /etc/systemd/system/nats-sink-prometheus-textfile.service
install_project_file examples/systemd/nats-sink-prometheus-textfile.timer 0644 root root /etc/systemd/system/nats-sink-prometheus-textfile.timer
install_project_file examples/systemd/nats-sink-prometheus-http.service 0644 root root /etc/systemd/system/nats-sink-prometheus-http.service
install_project_file examples/systemd/nats-sink-nats-monitoring.service 0644 root root /etc/systemd/system/nats-sink-nats-monitoring.service
install_project_file examples/systemd/nats-sink-nats-monitoring.timer 0644 root root /etc/systemd/system/nats-sink-nats-monitoring.timer
systemctl daemon-reload
systemctl enable nats-sink

echo "Installed nats-sink service for detected OS ID='$OS_ID'."
echo "Installed Python package: $NATS_SINKS_PACKAGE_SPEC"
echo "Installed service assets from ref: $NATS_SINKS_INSTALL_REF"
echo "Edit /etc/nats-sinks/config.json and /etc/nats-sinks/nats-sink.env, then run: systemctl start nats-sink"
echo "Prometheus textfile export is installed but disabled by policy and timer."
echo "To enable after policy review: systemctl enable --now nats-sink-prometheus-textfile.timer"
echo "Prometheus HTTP endpoint service is installed but disabled by policy and systemd."
echo "To enable after policy review: systemctl enable --now nats-sink-prometheus-http.service"
echo "NATS server monitoring snapshot service is installed but disabled by policy and timer."
echo "To enable after policy review: systemctl enable --now nats-sink-nats-monitoring.timer"
