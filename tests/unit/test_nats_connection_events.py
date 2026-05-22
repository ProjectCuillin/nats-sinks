# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for NATS connection event metrics.

The runner wraps nats-py connection callbacks so operational teams can see
disconnects, reconnects, closes, discovered servers, and asynchronous client
errors without changing delivery semantics or taking callback ownership away
from embedding applications.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from nats_sinks import NatsEnvelope
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames
from nats_sinks.core.runner import JetStreamSinkRunner


class NoopSink:
    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        del messages

    async def stop(self) -> None:
        return None


def _runner(
    *,
    metrics: InMemoryMetrics | None = None,
    nats_options: dict[str, object] | None = None,
) -> JetStreamSinkRunner:
    return JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-sink",
        subject="orders.*",
        sink=NoopSink(),
        metrics=metrics,
        nats_options=nats_options,
    )


@pytest.mark.asyncio
async def test_runner_installs_connection_event_metrics() -> None:
    metrics = InMemoryMetrics()
    runner = _runner(metrics=metrics)

    options = runner._nats_connect_options()

    await options["disconnected_cb"]()
    await options["reconnected_cb"]()
    await options["closed_cb"]()
    await options["discovered_server_cb"]()
    await options["error_cb"](RuntimeError("async protocol error"))

    assert metrics.counters[MetricNames.NATS_CONNECTION_DISCONNECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.NATS_CONNECTION_RECONNECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.NATS_CONNECTION_CLOSED_TOTAL] == 1
    assert metrics.counters[MetricNames.NATS_DISCOVERED_SERVERS_TOTAL] == 1
    assert metrics.counters[MetricNames.NATS_ASYNC_ERRORS_TOTAL] == 1


@pytest.mark.asyncio
async def test_runner_preserves_user_supplied_connection_callbacks() -> None:
    metrics = InMemoryMetrics()
    events: list[str] = []

    async def on_reconnected() -> None:
        events.append("reconnected")

    def on_error(error: BaseException) -> None:
        events.append(f"error:{type(error).__name__}")

    runner = _runner(
        metrics=metrics,
        nats_options={
            "reconnected_cb": on_reconnected,
            "error_cb": on_error,
        },
    )

    options = runner._nats_connect_options()

    await options["reconnected_cb"]()
    await options["error_cb"](RuntimeError("async protocol error"))

    assert events == ["reconnected", "error:RuntimeError"]
    assert metrics.counters[MetricNames.NATS_CONNECTION_RECONNECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.NATS_ASYNC_ERRORS_TOTAL] == 1


def test_runner_keeps_configured_servers_option() -> None:
    runner = _runner(nats_options={"servers": ["nats://nats-a.example:4222"]})

    options = runner._nats_connect_options()

    assert options["servers"] == ["nats://nats-a.example:4222"]
