# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Sequence

import pytest

from nats_sinks import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.sinks.registry import SinkRegistry


class MemorySink:
    """Small local sink used to keep registry tests independent."""

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.messages = list(messages)

    async def stop(self) -> None:
        return None


def test_registry_creates_registered_sink() -> None:
    registry = SinkRegistry()
    registry.register("memory", lambda _config: MemorySink())

    assert isinstance(registry.create("memory", {}), MemorySink)


def test_registry_rejects_unknown_sink() -> None:
    registry = SinkRegistry()

    with pytest.raises(ConfigurationError, match="unknown sink type"):
        registry.create("missing", {})
