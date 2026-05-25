# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nats_sinks.testing import (
    WebSocketHarnessPorts,
    nats_server_command,
    render_nats_websocket_config,
    sanitized_selected_ports,
    websocket_harness,
    write_nats_websocket_config,
)


def test_choose_loopback_port_uses_preferred_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        websocket_harness,
        "port_is_available",
        lambda port, *, host="127.0.0.1": port == 18080,
    )

    selected = websocket_harness.choose_loopback_port(18080)

    assert selected == 18080


def test_choose_loopback_port_avoids_occupied_preferred_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        websocket_harness,
        "port_is_available",
        lambda port, *, host="127.0.0.1": port != 18080,
    )
    monkeypatch.setattr(
        websocket_harness,
        "_allocate_ephemeral_port",
        lambda *, host, used: 19000,
    )

    selected = websocket_harness.choose_loopback_port(18080)

    assert selected == 19000


def test_choose_websocket_harness_ports_are_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        websocket_harness,
        "port_is_available",
        lambda port, *, host="127.0.0.1": True,
    )

    ports = websocket_harness.choose_websocket_harness_ports()

    assert len({ports.nats, ports.monitoring, ports.websocket}) == 3
    assert all(0 < port < 65536 for port in {ports.nats, ports.monitoring, ports.websocket})


def test_render_nats_websocket_config_contains_only_local_paths_and_ports(tmp_path: Path) -> None:
    store_dir = tmp_path / "jetstream"
    ports = WebSocketHarnessPorts(nats=14222, monitoring=18222, websocket=18080)

    rendered = render_nats_websocket_config(ports=ports, store_dir=store_dir)

    assert "server_name: nats_sinks_websocket_test" in rendered
    assert "host: 127.0.0.1" in rendered
    assert "port: 14222" in rendered
    assert "http: 18222" in rendered
    assert "port: 18080" in rendered
    assert f"store_dir: {json.dumps(str(store_dir))}" in rendered
    assert "no_tls: true" in rendered


def test_write_nats_websocket_config_creates_config_and_store_dir(tmp_path: Path) -> None:
    ports = WebSocketHarnessPorts(nats=14222, monitoring=18222, websocket=18080)

    config = write_nats_websocket_config(tmp_path / "run", ports=ports)

    assert config.config_path.is_file()
    assert config.store_dir.is_dir()
    assert config.websocket_url == "ws://127.0.0.1:18080"
    assert "websocket" in config.config_path.read_text(encoding="utf-8")


def test_nats_server_command_uses_fixed_argument_list(tmp_path: Path) -> None:
    config_path = tmp_path / "nats-websocket.conf"

    command = nats_server_command(config_path, executable="/usr/local/bin/nats-server")

    assert command == ["/usr/local/bin/nats-server", "-c", str(config_path)]


def test_sanitized_selected_ports_contains_no_paths_or_connection_secrets(tmp_path: Path) -> None:
    ports = WebSocketHarnessPorts(nats=14222, monitoring=18222, websocket=18080)
    config = write_nats_websocket_config(tmp_path / "run", ports=ports)

    rendered = sanitized_selected_ports(config)

    assert rendered == {
        "transport": "websocket",
        "nats_port": 14222,
        "monitoring_port": 18222,
        "websocket_port": 18080,
    }


async def test_wait_for_tcp_port_retries_with_injected_connector() -> None:
    attempts = 0
    sleep_delays: list[float] = []

    async def connector(host: str, port: int) -> None:
        nonlocal attempts
        assert host == "127.0.0.1"
        assert port == 18080
        attempts += 1
        if attempts < 3:
            raise OSError("listener not ready")

    async def sleep(delay: float) -> None:
        sleep_delays.append(delay)

    await websocket_harness.wait_for_tcp_port(
        "127.0.0.1",
        18080,
        timeout_seconds=1,
        connector=connector,
        sleep=sleep,
    )

    assert attempts == 3
    assert sleep_delays == [0.1, 0.1]


async def test_wait_for_tcp_port_rejects_invalid_timeout() -> None:
    async def connector(host: str, port: int) -> None:
        raise AssertionError(f"connector should not run for {host}:{port}")

    with pytest.raises(ValueError, match="timeout_seconds"):
        await websocket_harness.wait_for_tcp_port(
            "127.0.0.1",
            18080,
            timeout_seconds=0,
            connector=connector,
        )
