# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Helpers for local NATS WebSocket certification tests.

The WebSocket harness is intentionally local-lab only.  It never contacts a
remote server, never discovers private infrastructure, and never assumes fixed
ports.  Instead, it checks whether the conventional local NATS ports are free
and chooses alternative loopback ports when another developer service is
already running.

The helpers are side-effect-light so unit tests can prove port selection and
configuration rendering without starting a NATS process.  The companion script
`scripts/run-websocket-e2e.py` uses these helpers for the optional live test.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

LOCALHOST = "127.0.0.1"
DEFAULT_NATS_PORT = 4222
DEFAULT_MONITORING_PORT = 8222
DEFAULT_WEBSOCKET_PORT = 8080
TcpConnector = Callable[[str, int], Awaitable[None]]
SleepFunction = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class WebSocketHarnessPorts:
    """Loopback ports selected for one temporary local NATS server."""

    nats: int
    monitoring: int
    websocket: int


@dataclass(frozen=True)
class WebSocketHarnessConfig:
    """Filesystem locations and ports for one generated local harness config."""

    config_path: Path
    store_dir: Path
    ports: WebSocketHarnessPorts

    @property
    def websocket_url(self) -> str:
        """Return the local WebSocket URL used by the test client."""

        return f"ws://{LOCALHOST}:{self.ports.websocket}"


def port_is_available(port: int, *, host: str = LOCALHOST) -> bool:
    """Return true when a loopback TCP port can be bound now.

    This check is not a security boundary; it is a collision-avoidance helper
    for local testing.  The harness still records the selected ports so a rare
    race can be diagnosed from sanitized output.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _allocate_ephemeral_port(*, host: str, used: set[int]) -> int:
    """Ask the operating system for a free loopback port not already selected."""

    for _attempt in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
            port = int(probe.getsockname()[1])
        if port not in used:
            return port
    raise RuntimeError("failed to allocate a unique local port for the WebSocket harness")


def choose_loopback_port(
    preferred: int,
    *,
    host: str = LOCALHOST,
    used: set[int] | None = None,
) -> int:
    """Choose `preferred` when free, otherwise choose an ephemeral loopback port."""

    selected = used if used is not None else set()
    if preferred not in selected and port_is_available(preferred, host=host):
        selected.add(preferred)
        return preferred
    port = _allocate_ephemeral_port(host=host, used=selected)
    selected.add(port)
    return port


def choose_websocket_harness_ports(*, host: str = LOCALHOST) -> WebSocketHarnessPorts:
    """Return collision-safe loopback ports for NATS, monitoring, and WebSocket."""

    used: set[int] = set()
    return WebSocketHarnessPorts(
        nats=choose_loopback_port(DEFAULT_NATS_PORT, host=host, used=used),
        monitoring=choose_loopback_port(DEFAULT_MONITORING_PORT, host=host, used=used),
        websocket=choose_loopback_port(DEFAULT_WEBSOCKET_PORT, host=host, used=used),
    )


def render_nats_websocket_config(*, ports: WebSocketHarnessPorts, store_dir: Path) -> str:
    """Render a minimal NATS config with JetStream and local WebSocket enabled."""

    store = json.dumps(str(store_dir))
    return (
        "server_name: nats_sinks_websocket_test\n"
        f"host: {LOCALHOST}\n"
        f"port: {ports.nats}\n"
        f"http: {ports.monitoring}\n"
        "jetstream {\n"
        f"  store_dir: {store}\n"
        "}\n"
        "websocket {\n"
        f"  host: {LOCALHOST}\n"
        f"  port: {ports.websocket}\n"
        "  no_tls: true\n"
        "}\n"
    )


def write_nats_websocket_config(
    base_dir: Path,
    *,
    ports: WebSocketHarnessPorts,
) -> WebSocketHarnessConfig:
    """Write the temporary NATS config and return its paths.

    Callers provide `base_dir` so scripts can keep generated material under
    `.local/` or another ignored location.  The generated config contains only
    loopback ports and a local JetStream store path.
    """

    root = base_dir.resolve()
    store_dir = root / "jetstream"
    config_path = root / "nats-websocket.conf"
    store_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        render_nats_websocket_config(ports=ports, store_dir=store_dir),
        encoding="utf-8",
    )
    return WebSocketHarnessConfig(config_path=config_path, store_dir=store_dir, ports=ports)


def nats_server_command(config_path: Path, *, executable: str = "nats-server") -> list[str]:
    """Return the fixed argument-list command for the local NATS test server."""

    return [executable, "-c", str(config_path)]


def sanitized_selected_ports(config: WebSocketHarnessConfig) -> dict[str, int | str]:
    """Return secret-free selected-port output for logs and test reports."""

    return {
        "transport": "websocket",
        "nats_port": config.ports.nats,
        "monitoring_port": config.ports.monitoring,
        "websocket_port": config.ports.websocket,
    }


async def _open_and_close_tcp(host: str, port: int) -> None:
    """Open and immediately close a loopback TCP connection."""

    _reader, writer = await asyncio.open_connection(host, port)
    writer.close()
    await writer.wait_closed()


async def wait_for_tcp_port(
    host: str,
    port: int,
    *,
    timeout_seconds: float,
    connector: TcpConnector | None = None,
    sleep: SleepFunction = asyncio.sleep,
) -> None:
    """Wait until a local TCP listener accepts connections.

    The live WebSocket e2e script starts a temporary NATS process and then waits
    for the WebSocket listener before handing control to `nats-py`.  This keeps
    normal successful runs free from noisy transient client tracebacks during
    process startup.  Unit tests inject `connector` and `sleep` so they can
    exercise retry behavior without opening real sockets.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    deadline = time.monotonic() + timeout_seconds
    connect = connector or _open_and_close_tcp
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            await connect(host, port)
            return
        except OSError as exc:
            last_error = exc
            await sleep(0.1)
    raise TimeoutError(
        f"local TCP listener {host}:{port} did not become available within timeout"
    ) from last_error
