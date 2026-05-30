# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Disconnected backend spool-and-replay certification helpers.

The helpers in this module model the operational pattern used when a final
backend is temporarily unreachable: commit messages to encrypted local spool
custody, replay them after recovery, and verify the final destination through
destination-specific assertions.  They deliberately avoid raw NATS clients and
ACK primitives.  During the spool phase, local spool commit is the durable
boundary; replay to the final sink is a separate at-least-once workflow.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import NatsSinksError
from nats_sinks.sinks.base import Sink
from nats_sinks.spool import SpoolSink, replay_spool_to_sink

DISCONNECTED_REPLAY_DEFAULT_MESSAGES_PER_PHASE = 1001
DISCONNECTED_REPLAY_MAX_MESSAGES_PER_PHASE = 10_000
DISCONNECTED_REPLAY_SCHEMA_VERSION = 1


class DisconnectedReplayBackend(Protocol):
    """Destination-specific adapter used by the disconnected certification."""

    name: str

    def reachable_sink(self) -> Sink:
        """Return a fresh sink configured for the reachable backend."""

    def unreachable_sink(self) -> Sink:
        """Return a fresh sink that must fail as if the backend is unreachable."""

    async def assert_expected_records(self, messages: Sequence[NatsEnvelope]) -> None:
        """Assert that the backend contains every expected synthetic message."""


@dataclass(frozen=True, slots=True)
class DisconnectedSpoolReplayOptions:
    """Configuration for one disconnected spool-and-replay certification run."""

    stream: str
    messages_per_phase: int = DISCONNECTED_REPLAY_DEFAULT_MESSAGES_PER_PHASE
    subject_prefix: str = "certification.disconnected"
    spool_key_b64: str = field(
        default_factory=lambda: base64.b64encode(b"nats-sinks-disconnected-key-v001").decode(
            "ascii"
        )
    )

    def __post_init__(self) -> None:
        """Validate bounded synthetic test settings."""

        if not self.stream.strip():
            raise ValueError("disconnected replay stream must not be empty")
        if self.messages_per_phase < 1:
            raise ValueError("messages_per_phase must be at least 1")
        if self.messages_per_phase > DISCONNECTED_REPLAY_MAX_MESSAGES_PER_PHASE:
            raise ValueError(
                f"messages_per_phase must not exceed {DISCONNECTED_REPLAY_MAX_MESSAGES_PER_PHASE}"
            )
        if not self.subject_prefix.strip():
            raise ValueError("subject_prefix must not be empty")


@dataclass(frozen=True, slots=True)
class DisconnectedSpoolReplayReport:
    """Sanitized evidence for one disconnected replay certification run."""

    backend: str
    stream: str
    messages_per_phase: int
    direct_before_records: int
    spooled_records: int
    replayed_records: int
    direct_after_records: int
    expected_backend_records: int
    unique_idempotency_keys: int
    spool_remaining_records: int
    outage_proved: bool
    schema_version: int = DISCONNECTED_REPLAY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, int | str | bool]:
        """Return a deterministic payload-free report."""

        return {
            "schema_version": self.schema_version,
            "backend": self.backend,
            "stream": self.stream,
            "messages_per_phase": self.messages_per_phase,
            "direct_before_records": self.direct_before_records,
            "spooled_records": self.spooled_records,
            "replayed_records": self.replayed_records,
            "direct_after_records": self.direct_after_records,
            "expected_backend_records": self.expected_backend_records,
            "unique_idempotency_keys": self.unique_idempotency_keys,
            "spool_remaining_records": self.spool_remaining_records,
            "outage_proved": self.outage_proved,
        }

    def to_json(self) -> str:
        """Render the sanitized report for local evidence files."""

        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def disconnected_replay_envelopes(
    *,
    options: DisconnectedSpoolReplayOptions,
    phase: str,
    sequence_offset: int,
) -> tuple[NatsEnvelope, ...]:
    """Build one deterministic synthetic phase of disconnected replay messages."""

    messages: list[NatsEnvelope] = []
    for index in range(1, options.messages_per_phase + 1):
        sequence = sequence_offset + index
        event_id = f"{options.stream}-{phase}-{index:04d}"
        payload = {
            "synthetic": True,
            "event_id": event_id,
            "phase": phase,
            "sequence": index,
            "storage": "disconnected-spool-replay-certification",
        }
        messages.append(
            NatsEnvelope(
                subject=f"{options.subject_prefix}.{phase}.{index:04d}",
                data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                headers={"Nats-Msg-Id": event_id},
                stream=options.stream,
                consumer="disconnected-spool-replay",
                stream_sequence=sequence,
                consumer_sequence=sequence,
                timestamp=datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
                message_id=event_id,
                redelivered=False,
                pending=0,
                priority="routine",
                classification="NATO UNCLASSIFIED",
                labels=("disconnected-replay", phase),
            )
        )
    return tuple(messages)


async def run_disconnected_spool_replay_certification(
    backend: DisconnectedReplayBackend,
    *,
    spool_directory: Path,
    options: DisconnectedSpoolReplayOptions,
) -> DisconnectedSpoolReplayReport:
    """Run the 1001 + 1001 + 1001 disconnected replay certification flow."""

    first = disconnected_replay_envelopes(
        options=options,
        phase="direct-before-outage",
        sequence_offset=0,
    )
    spooled = disconnected_replay_envelopes(
        options=options,
        phase="spooled-during-outage",
        sequence_offset=options.messages_per_phase,
    )
    final = disconnected_replay_envelopes(
        options=options,
        phase="direct-after-recovery",
        sequence_offset=options.messages_per_phase * 2,
    )
    outage_probe = disconnected_replay_envelopes(
        options=DisconnectedSpoolReplayOptions(
            stream=f"{options.stream}_OUTAGE_PROBE",
            messages_per_phase=1,
            subject_prefix=options.subject_prefix,
            spool_key_b64=options.spool_key_b64,
        ),
        phase="outage-probe",
        sequence_offset=0,
    )

    await _write_with_fresh_sink(backend.reachable_sink, first)
    outage_proved = await _expect_backend_unavailable(backend.unreachable_sink, outage_probe)

    spool = SpoolSink(
        directory=spool_directory,
        fsync=False,
        encryption={
            "enabled": True,
            "algorithm": "aes-256-gcm",
            "key_id": "disconnected-spool-replay-certification",
            "key_b64": options.spool_key_b64,
        },
    )
    await spool.start()
    try:
        await spool.write_batch(spooled)
        spooled_records = await _spool_count(spool)
        replay_sink = backend.reachable_sink()
        await replay_sink.start()
        try:
            await _maybe_healthcheck(replay_sink)
            replay_result = await replay_spool_to_sink(spool, replay_sink)
        finally:
            await replay_sink.stop()
        remaining = await _spool_count(spool)
    finally:
        await spool.stop()

    await _write_with_fresh_sink(backend.reachable_sink, final)
    expected_messages = (*first, *spooled, *final)
    await backend.assert_expected_records(expected_messages)

    return DisconnectedSpoolReplayReport(
        backend=backend.name,
        stream=options.stream,
        messages_per_phase=options.messages_per_phase,
        direct_before_records=len(first),
        spooled_records=spooled_records,
        replayed_records=replay_result.replayed_records,
        direct_after_records=len(final),
        expected_backend_records=len(expected_messages),
        unique_idempotency_keys=len({message.idempotency_key() for message in expected_messages}),
        spool_remaining_records=remaining,
        outage_proved=outage_proved,
    )


async def _write_with_fresh_sink(
    sink_factory: Callable[[], Sink],
    messages: Sequence[NatsEnvelope],
) -> None:
    sink = sink_factory()
    await sink.start()
    try:
        await _maybe_healthcheck(sink)
        await sink.write_batch(messages)
    finally:
        await sink.stop()


async def _expect_backend_unavailable(
    sink_factory: Callable[[], Sink],
    messages: Sequence[NatsEnvelope],
) -> bool:
    sink = sink_factory()
    try:
        await sink.start()
        await _maybe_healthcheck(sink)
        await sink.write_batch(messages)
    except NatsSinksError:
        return True
    finally:
        try:
            await sink.stop()
        except NatsSinksError:
            pass
    raise AssertionError("unreachable backend sink unexpectedly accepted a write")


async def _maybe_healthcheck(sink: Sink) -> None:
    healthcheck = getattr(sink, "healthcheck", None)
    if healthcheck is None or not callable(healthcheck):
        return
    result = healthcheck()
    if hasattr(result, "__await__"):
        await result


async def _spool_count(spool: SpoolSink) -> int:
    return len(await asyncio.to_thread(spool.committed_entries))
