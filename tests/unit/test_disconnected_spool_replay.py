# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import DestinationUnavailableError
from nats_sinks.testing.disconnected_spool_replay import (
    DisconnectedSpoolReplayOptions,
    run_disconnected_spool_replay_certification,
)


class MemoryReplaySink:
    """In-memory sink used to prove disconnected replay without network calls."""

    def __init__(
        self,
        records: dict[str, NatsEnvelope],
        *,
        available: bool = True,
    ) -> None:
        self.records = records
        self.available = available

    async def start(self) -> None:
        if not self.available:
            raise DestinationUnavailableError("synthetic backend unavailable")

    async def healthcheck(self) -> None:
        if not self.available:
            raise DestinationUnavailableError("synthetic backend unavailable")

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        if not self.available:
            raise DestinationUnavailableError("synthetic backend unavailable")
        for message in messages:
            self.records.setdefault(message.idempotency_key(), message)

    async def stop(self) -> None:
        return None


class MemoryReplayBackend:
    """Backend adapter for the deterministic disconnected replay tests."""

    name = "memory"

    def __init__(self) -> None:
        self.records: dict[str, NatsEnvelope] = {}

    def reachable_sink(self) -> MemoryReplaySink:
        return MemoryReplaySink(self.records, available=True)

    def unreachable_sink(self) -> MemoryReplaySink:
        return MemoryReplaySink(self.records, available=False)

    async def assert_expected_records(self, messages: Sequence[NatsEnvelope]) -> None:
        expected = {message.idempotency_key() for message in messages}
        assert set(self.records) == expected


@pytest.mark.asyncio
async def test_disconnected_spool_replay_certifies_1001_message_phases(tmp_path: Path) -> None:
    backend = MemoryReplayBackend()
    options = DisconnectedSpoolReplayOptions(
        stream="DISCONNECTED_REPLAY_UNIT",
        messages_per_phase=1001,
    )

    report = await run_disconnected_spool_replay_certification(
        backend,
        spool_directory=tmp_path / "spool",
        options=options,
    )

    assert report.backend == "memory"
    assert report.messages_per_phase == 1001
    assert report.direct_before_records == 1001
    assert report.spooled_records == 1001
    assert report.replayed_records == 1001
    assert report.direct_after_records == 1001
    assert report.expected_backend_records == 3003
    assert report.unique_idempotency_keys == 3003
    assert report.spool_remaining_records == 0
    assert report.outage_proved is True
    assert len(backend.records) == 3003


@pytest.mark.asyncio
async def test_disconnected_spool_replay_fails_if_outage_sink_accepts_write(
    tmp_path: Path,
) -> None:
    class UnsafeBackend(MemoryReplayBackend):
        def unreachable_sink(self) -> MemoryReplaySink:
            return MemoryReplaySink(self.records, available=True)

    with pytest.raises(AssertionError, match="unexpectedly accepted"):
        await run_disconnected_spool_replay_certification(
            UnsafeBackend(),
            spool_directory=tmp_path / "spool",
            options=DisconnectedSpoolReplayOptions(
                stream="DISCONNECTED_REPLAY_UNSAFE",
                messages_per_phase=3,
            ),
        )
