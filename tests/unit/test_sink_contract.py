# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Sequence

from nats_sinks import NatsEnvelope, Sink


class MemorySink:
    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.messages = list(messages)

    async def stop(self) -> None:
        return None


def test_sink_protocol_runtime_checkable() -> None:
    assert isinstance(MemorySink(), Sink)
