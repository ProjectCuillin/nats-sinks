# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the documented Linux systemd installer scripts.

The installer cannot be executed in unit tests because it needs root, package
manager access, and systemd.  These tests still protect the public operator
contract: there is one real installer, it detects supported Linux families, and
the legacy distribution-specific entry points remain thin compatibility
wrappers.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_unified_systemd_installer_detects_supported_linux_families() -> None:
    script = _read("scripts/install-systemd.sh")

    assert "/etc/os-release" in script
    assert "NATS_SINKS_INSTALL_REF" in script
    assert "NATS_SINKS_PACKAGE_SPEC" in script
    assert "LOCAL_ASSETS_AVAILABLE=false" in script
    assert "grep -q 'name = \"nats-sinks\"'" in script
    assert "raw.githubusercontent.com/ProjectCuillin/nats-sinks" in script
    assert "install_project_file" in script
    assert "curl -fsSL" in script
    assert "nats-sinks==${NATS_SINKS_INSTALL_REF#v}" in script
    assert 'pip install "$NATS_SINKS_PACKAGE_SPEC"' in script
    assert "apt-get install -y python3 python3-venv python3-pip curl" in script
    assert "dnf install -y python3 python3-pip curl" in script
    assert "Unsupported Linux distribution" in script
    assert "nats-sink-prometheus-textfile.timer" in script
    assert "nats-sink-prometheus-http.service" in script
    assert "nats-sink-nats-monitoring.service" in script
    assert "nats-sink-nats-monitoring.timer" in script
    assert "systemctl enable nats-sink" in script
    assert "systemctl enable --now nats-sink-prometheus-textfile.timer" in script
    assert "systemctl enable --now nats-sink-prometheus-http.service" in script
    assert "systemctl enable --now nats-sink-nats-monitoring.timer" in script
    assert "Installed service assets from ref" in script


def test_legacy_systemd_installers_delegate_to_unified_script() -> None:
    debian = _read("scripts/install-systemd-debian.sh")
    oracle = _read("scripts/install-systemd-oracle-linux.sh")

    assert 'exec "$SCRIPT_DIR/install-systemd.sh"' in debian
    assert "deprecated" in debian
    assert 'exec "$SCRIPT_DIR/install-systemd.sh"' in oracle
    assert "deprecated" in oracle
