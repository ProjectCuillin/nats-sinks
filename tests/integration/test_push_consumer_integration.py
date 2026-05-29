# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Optional live NATS certification for the push-consumer runner.

The test is deliberately disabled by default.  It is intended for maintainers
who have started a local, disposable NATS server with JetStream enabled and
want real client/server evidence in addition to the mocked delivery-contract
unit tests.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from contextlib import suppress

import pytest

from nats_sinks.core.config import (
    ConsumerManagementConfig,
    DeliveryConfig,
    PushConsumerConfig,
)
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.sinks.base import Sink


def _push_consumer_integration_enabled() -> bool:
    return os.getenv("NATS_SINKS_PUSH_CONSUMER_INTEGRATION") == "1"


class StopAfterWriteSink(Sink):
    """Capture delivered envelopes and stop the runner after the expected write."""

    def __init__(self) -> None:
        self.messages: list[NatsEnvelope] = []
        self.runner: JetStreamSinkRunner | None = None

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.messages.extend(messages)
        if self.runner is not None:
            self.runner.request_stop()

    async def stop(self) -> None:
        return None


@pytest.mark.integration
@pytest.mark.skipif(
    not _push_consumer_integration_enabled(),
    reason="set NATS_SINKS_PUSH_CONSUMER_INTEGRATION=1 to run live push-consumer tests",
)
async def test_live_nats_push_consumer_ack_after_sink_success() -> None:
    """Prove live push delivery reaches the sink before the runner ACKs."""

    nats = pytest.importorskip("nats")
    from nats.js.api import StreamConfig  # noqa: PLC0415 - optional live-test path.

    run_id = uuid.uuid4().hex
    nats_url = os.getenv("NATS_SINKS_PUSH_CONSUMER_NATS_URL", "nats://127.0.0.1:4222")
    stream = f"NATS_SINKS_PUSH_{run_id[:16].upper()}"
    subject = f"nats_sinks.push.{run_id}"
    deliver_subject = f"_INBOX.nats_sinks.push.{run_id}"
    consumer = f"push-cert-{run_id[:16]}"

    nc = await nats.connect(nats_url)
    js = nc.jetstream()
    sink = StopAfterWriteSink()
    try:
        await js.add_stream(StreamConfig(name=stream, subjects=[subject]))
        await js.publish(
            subject,
            b'{"kind":"push-certification","run":"local"}',
            headers={"Nats-Msg-Id": f"push-cert-{run_id}"},
        )

        runner = JetStreamSinkRunner(
            nats_url=nats_url,
            stream=stream,
            consumer=consumer,
            subject=subject,
            sink=sink,
            jetstream=js,
            delivery=DeliveryConfig(batch_size=1, batch_timeout_ms=25),
            consumer_management=ConsumerManagementConfig(mode="create_if_missing"),
            push_consumer=PushConsumerConfig(
                enabled=True,
                deliver_subject=deliver_subject,
                pending_msgs_limit=8,
                pending_bytes_limit=65_536,
                flow_control=True,
                idle_heartbeat_seconds=1.0,
            ),
        )
        sink.runner = runner

        await runner.run()

        assert len(sink.messages) == 1
        assert sink.messages[0].subject == subject
        assert sink.messages[0].payload_as_json() == {
            "kind": "push-certification",
            "run": "local",
        }
    finally:
        with suppress(Exception):
            await js.delete_stream(stream)
        await nc.close()
