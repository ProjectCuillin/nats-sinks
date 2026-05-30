# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import inspect
import os
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nats_sinks.coherence import CoherenceSink, CoherenceSinkConfig
from nats_sinks.coherence.mapping import coherence_key_for_envelope
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.testing.disconnected_spool_replay import (
    DisconnectedSpoolReplayOptions,
    run_disconnected_spool_replay_certification,
)


def _coherence_enabled() -> bool:
    return os.getenv("NATS_SINKS_COHERENCE_INTEGRATION") == "1"


def _disconnected_replay_enabled() -> bool:
    return os.getenv("NATS_SINKS_COHERENCE_DISCONNECTED_REPLAY") == "1"


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


class CoherenceDisconnectedReplayBackend:
    """Adapter used by the disconnected spool-and-replay certification."""

    name = "Oracle Coherence Community Edition"

    def __init__(self, *, config: CoherenceSinkConfig) -> None:
        self.config = config

    def reachable_sink(self) -> CoherenceSink:
        return CoherenceSink(config=self.config)

    def unreachable_sink(self) -> CoherenceSink:
        config = self.config.model_copy(
            update={
                "address": "127.0.0.1:1",
                "request_timeout_seconds": 1.0,
                "ready_timeout_seconds": 1.0,
            }
        )
        return CoherenceSink(config=config)

    async def assert_expected_records(self, messages: Sequence[NatsEnvelope]) -> None:
        session, cache = await _coherence_cache(
            address=self.config.address,
            cache_name=self.config.cache_name,
        )
        try:
            missing = []
            for message in messages:
                key = coherence_key_for_envelope(message, config=self.config)
                actual = await _maybe_await(cache.get(key))
                if actual is None:
                    missing.append(message.idempotency_key())
            assert not missing, f"missing Oracle Coherence records: {len(missing)}"
        finally:
            close = getattr(session, "close", None)
            if close is not None:
                await _maybe_await(close())


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


@pytest.mark.asyncio
async def test_coherence_sink_disconnected_spool_replay_certification(
    tmp_path: Path,
) -> None:
    """Certify Oracle Coherence replay after local spool custody."""

    if not _disconnected_replay_enabled():
        pytest.skip("set NATS_SINKS_COHERENCE_DISCONNECTED_REPLAY=1 to run disconnected replay")
    address = _env("NATS_SINKS_COHERENCE_ADDRESS", "127.0.0.1:1408")
    cache_name = _env("NATS_SINKS_COHERENCE_CACHE_NAME", "nats_sinks_sink_e2e")
    config = CoherenceSinkConfig(
        address=address,
        cache_name=cache_name,
        duplicate_policy="replace",
        request_timeout_seconds=10.0,
        ready_timeout_seconds=10.0,
    )
    stream = f"COHERENCE_DISC_{uuid.uuid4().hex[:12].upper()}"

    report = await run_disconnected_spool_replay_certification(
        CoherenceDisconnectedReplayBackend(config=config),
        spool_directory=tmp_path / "spool",
        options=DisconnectedSpoolReplayOptions(stream=stream),
    )

    assert report.backend == "Oracle Coherence Community Edition"
    assert report.expected_backend_records == 3003
    assert report.spool_remaining_records == 0
    assert report.outage_proved is True
