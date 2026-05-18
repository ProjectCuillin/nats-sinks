# SPDX-License-Identifier: Apache-2.0
"""Destination sink protocols.

The `Sink` protocol is intentionally small: start, write a batch, stop.  A sink
must return from `write_batch` only after the destination state is durably
committed or otherwise complete.  If it cannot make that guarantee, it must
raise a framework-defined error.

Optional protocols let richer destinations expose health checks, explicit
schema setup, or flushing without forcing every sink to implement those
features.  None of these protocols include JetStream acknowledgement methods;
ACK ownership belongs exclusively to the core runner.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from nats_sinks.core.envelope import NatsEnvelope


@runtime_checkable
class Sink(Protocol):
    """Minimal destination sink contract."""

    async def start(self) -> None:
        """Initialize destination resources."""

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Durably write and commit a batch before returning success."""

    async def stop(self) -> None:
        """Release destination resources."""


@runtime_checkable
class HealthCheckableSink(Protocol):
    """Optional sink health-check interface."""

    async def healthcheck(self) -> None:
        """Verify destination availability."""


@runtime_checkable
class SchemaAwareSink(Protocol):
    """Optional sink schema-management interface."""

    async def ensure_schema(self) -> None:
        """Create or validate destination schema when explicitly enabled."""


@runtime_checkable
class FlushableSink(Protocol):
    """Optional sink flush interface."""

    async def flush(self) -> None:
        """Flush buffered writes."""
