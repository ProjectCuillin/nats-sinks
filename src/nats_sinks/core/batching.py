# SPDX-License-Identifier: Apache-2.0
"""Batching utilities used by the pull-consumer runtime.

Batching is one of the main backpressure controls in nats-sinks.  The runner
fetches bounded batches from JetStream and hands those batches to a sink as a
single durable write unit.  These helpers are intentionally small and
deterministic so they can be tested without NATS, Oracle, timers, or network
state.

The functions in this module do not ACK, NAK, retry, sleep, or inspect message
payloads.  They only partition local Python collections.  Runtime policy such
as timeout handling, maximum in-flight batches, and failure classification lives
in the runner.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TypeVar

from nats_sinks.core.errors import ConfigurationError

T = TypeVar("T")


def chunked(items: Sequence[T], batch_size: int) -> list[list[T]]:
    """Split a finite sequence into bounded batches."""

    if batch_size < 1:
        raise ConfigurationError("batch_size must be at least 1")
    return [list(items[index : index + batch_size]) for index in range(0, len(items), batch_size)]


def iter_chunked(items: Iterable[T], batch_size: int) -> Iterable[list[T]]:
    """Yield bounded batches from any iterable."""

    if batch_size < 1:
        raise ConfigurationError("batch_size must be at least 1")
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch
