# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import inspect
import os
from datetime import UTC, datetime
from typing import Any

import pytest

from nats_sinks.coherence import CoherenceSink, CoherenceSinkConfig
from nats_sinks.coherence.mapping import coherence_key_for_envelope
from nats_sinks.core.envelope import NatsEnvelope


def _coherence_enabled() -> bool:
    return os.getenv("NATS_SINKS_COHERENCE_INTEGRATION") == "1"


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _coherence_enabled(),
        reason="set NATS_SINKS_COHERENCE_INTEGRATION=1 to run Oracle Coherence CE e2e tests",
    ),
]


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value else default


def _envelope() -> NatsEnvelope:
    return NatsEnvelope(
        subject="coherence.integration.event",
        data=b'{"event_id":"COHERENCE-E2E-1","status":"ok"}',
        headers={"Nats-Msg-Id": "coherence-e2e-message-1"},
        stream="COHERENCE_E2E",
        consumer="coherence-e2e",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        message_id="coherence-e2e-message-1",
        redelivered=False,
        pending=0,
        priority="normal",
        classification="NATO UNCLASSIFIED",
        labels=("coherence-e2e", "local-test"),
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _coherence_cache(*, address: str, cache_name: str) -> tuple[Any, Any]:
    coherence = importlib.import_module("coherence")
    options = coherence.Options(
        address=address,
        request_timeout_seconds=10.0,
        ready_timeout_seconds=10.0,
    )
    session = await _maybe_await(coherence.Session.create(options))
    cache = await _maybe_await(session.get_cache(cache_name))
    return session, cache


@pytest.mark.asyncio
async def test_coherence_sink_writes_json_value_to_container_backend() -> None:
    address = _env("NATS_SINKS_COHERENCE_ADDRESS", "127.0.0.1:1408")
    cache_name = _env("NATS_SINKS_COHERENCE_CACHE_NAME", "nats_sinks_sink_e2e")
    envelope = _envelope()
    config = CoherenceSinkConfig(
        address=address,
        cache_name=cache_name,
        duplicate_policy="replace",
        request_timeout_seconds=10.0,
        ready_timeout_seconds=10.0,
    )
    key = coherence_key_for_envelope(envelope, config=config)
    sink = CoherenceSink(config=config)

    await sink.start()
    try:
        await sink.write_batch([envelope])
    finally:
        await sink.stop()

    session, cache = await _coherence_cache(address=address, cache_name=cache_name)
    try:
        actual = await _maybe_await(cache.get(key))
        assert actual["schema"] == "nats_sinks.coherence.event.v1"
        assert actual["payload"] == {"event_id": "COHERENCE-E2E-1", "status": "ok"}
        assert actual["classification"] == "NATO UNCLASSIFIED"
        await _maybe_await(cache.remove(key))
    finally:
        close = getattr(session, "close", None)
        if close is not None:
            await _maybe_await(close())
