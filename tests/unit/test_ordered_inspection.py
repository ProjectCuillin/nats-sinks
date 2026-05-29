# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the read-only ordered-consumer inspection path."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.ordered_inspection import (
    OrderedInspectionOptions,
    collect_ordered_inspection_records,
    detect_ordered_consumer_capability,
    ordered_consumer_supported,
    render_ordered_inspection_jsonl,
    resolve_inspection_output_path,
)


@dataclass
class FakeSequence:
    stream: int = 1
    consumer: int = 1


@dataclass
class FakeMetadata:
    stream: str = "ORDERS"
    consumer: str = "_ordered"
    sequence: FakeSequence = field(default_factory=FakeSequence)
    timestamp: datetime = field(default_factory=lambda: datetime(2026, 5, 26, tzinfo=UTC))
    num_delivered: int = 1
    num_pending: int = 0


@dataclass
class FakeMessage:
    subject: str = "orders.created"
    data: bytes = b'{"order_id":"A-100"}'
    headers: dict[str, str] = field(default_factory=dict)
    metadata: FakeMetadata = field(default_factory=FakeMetadata)
    reply: str | None = None
    ack_called: bool = False

    async def ack(self) -> None:
        self.ack_called = True
        raise AssertionError("ordered inspection must not ACK messages")


class FakeSubscription:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = list(messages)
        self.unsubscribed = False

    async def next_msg(self, **kwargs: float) -> FakeMessage:
        _ = kwargs["timeout"]
        if not self.messages:
            raise TimeoutError
        return self.messages.pop(0)

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeOrderedJetStream:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.subscription = FakeSubscription(messages)
        self.subscribe_call: dict[str, Any] | None = None

    async def subscribe(
        self,
        subject: str,
        *,
        stream: str,
        ordered_consumer: bool,
        manual_ack: bool,
        idle_heartbeat: float | None,
        pending_msgs_limit: int,
        pending_bytes_limit: int,
    ) -> FakeSubscription:
        self.subscribe_call = {
            "subject": subject,
            "stream": stream,
            "ordered_consumer": ordered_consumer,
            "manual_ack": manual_ack,
            "idle_heartbeat": idle_heartbeat,
            "pending_msgs_limit": pending_msgs_limit,
            "pending_bytes_limit": pending_bytes_limit,
        }
        return self.subscription


class FakeUnsupportedJetStream:
    async def subscribe(self, subject: str) -> None:
        _ = subject


class FakeNonCallableSubscribeJetStream:
    subscribe = object()


class FakeAmbiguousSubscribe:
    @property
    def __signature__(self) -> object:
        raise ValueError("private parser detail")

    def __call__(self, subject: str) -> None:
        _ = subject


class FakeAmbiguousJetStream:
    subscribe = FakeAmbiguousSubscribe()


def test_ordered_consumer_capability_detection_is_fail_closed() -> None:
    assert ordered_consumer_supported(FakeOrderedJetStream([])) is True
    assert ordered_consumer_supported(FakeUnsupportedJetStream()) is False
    assert ordered_consumer_supported(FakeNonCallableSubscribeJetStream()) is False
    assert ordered_consumer_supported(FakeAmbiguousJetStream()) is False
    assert ordered_consumer_supported(object()) is False


@pytest.mark.parametrize(
    ("jetstream", "supported", "reason"),
    [
        (
            FakeOrderedJetStream([]),
            True,
            "JetStream subscribe API exposes ordered_consumer",
        ),
        (
            FakeUnsupportedJetStream(),
            False,
            "JetStream subscribe API does not expose ordered_consumer",
        ),
        (
            FakeNonCallableSubscribeJetStream(),
            False,
            "JetStream subscribe attribute is not callable",
        ),
        (
            FakeAmbiguousJetStream(),
            False,
            "JetStream subscribe signature is unavailable",
        ),
        (
            object(),
            False,
            "JetStream context does not expose subscribe",
        ),
    ],
)
def test_ordered_consumer_capability_result_names_client_state(
    jetstream: object,
    supported: bool,
    reason: str,
) -> None:
    result = detect_ordered_consumer_capability(jetstream)

    assert result.supported is supported
    assert result.checked_api == "JetStreamContext.subscribe"
    assert result.reason == reason


async def test_ordered_inspection_redacts_payload_and_sensitive_headers() -> None:
    message = FakeMessage(
        headers={
            "Authorization": "Bearer example",
            "Nats-Msg-Id": "MSG-1",
            "X-Unit": "visible",
        }
    )
    jetstream = FakeOrderedJetStream([message])

    result = await collect_ordered_inspection_records(
        jetstream,
        subject="orders.created",
        stream="ORDERS",
    )

    assert jetstream.subscribe_call == {
        "subject": "orders.created",
        "stream": "ORDERS",
        "ordered_consumer": True,
        "manual_ack": False,
        "idle_heartbeat": None,
        "pending_msgs_limit": 128,
        "pending_bytes_limit": 8388608,
    }
    assert result.messages_seen == 1
    assert result.stopped_reason == "timeout"
    record = result.records[0].to_dict()
    assert record["inspection_only"] is True
    assert record["headers"]["Authorization"] == "<redacted>"
    assert record["headers"]["X-Unit"] == "visible"
    assert record["payload"]["redacted"] is True
    assert "data" not in record["payload"]
    assert message.ack_called is False
    assert jetstream.subscription.unsubscribed is True


async def test_ordered_inspection_can_include_payload_when_explicit() -> None:
    jetstream = FakeOrderedJetStream([FakeMessage(data=b"clear local sample")])

    result = await collect_ordered_inspection_records(
        jetstream,
        subject="orders.created",
        stream="ORDERS",
        options=OrderedInspectionOptions(include_payload=True),
    )

    payload = result.records[0].payload
    assert payload["redacted"] is False
    assert payload["encoding"] == "utf-8"
    assert payload["data"] == "clear local sample"


async def test_ordered_inspection_stops_before_payload_byte_limit() -> None:
    jetstream = FakeOrderedJetStream([FakeMessage(data=b"12345")])

    result = await collect_ordered_inspection_records(
        jetstream,
        subject="orders.created",
        stream="ORDERS",
        options=OrderedInspectionOptions(max_payload_bytes=4),
    )

    assert result.messages_seen == 0
    assert result.payload_bytes_seen == 0
    assert result.stopped_reason == "max_payload_bytes"


async def test_ordered_inspection_fails_closed_without_client_support() -> None:
    with pytest.raises(
        ConfigurationError,
        match="does not expose ordered_consumer",
    ):
        await collect_ordered_inspection_records(
            FakeUnsupportedJetStream(),
            subject="orders.created",
            stream="ORDERS",
        )


async def test_ordered_inspection_fails_closed_when_client_support_is_ambiguous() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        await collect_ordered_inspection_records(
            FakeAmbiguousJetStream(),
            subject="orders.created",
            stream="ORDERS",
        )

    message = str(exc_info.value)
    assert "ordered-consumer inspection requires" in message
    assert "signature is unavailable" in message
    assert "private parser detail" not in message
    assert "orders.created" not in message
    assert "ORDERS" not in message


def test_ordered_inspection_output_path_must_stay_under_root(tmp_path: Path) -> None:
    root = tmp_path / "inspection"

    resolved = resolve_inspection_output_path(Path("orders.jsonl"), output_root=root)

    assert resolved == root.resolve() / "orders.jsonl"
    with pytest.raises(ConfigurationError, match="inside the output root"):
        resolve_inspection_output_path(Path("../outside.jsonl"), output_root=root)
    with pytest.raises(ConfigurationError, match=r"end with \.jsonl"):
        resolve_inspection_output_path(Path("orders.txt"), output_root=root)


async def test_ordered_inspection_jsonl_is_sanitized_and_parseable() -> None:
    message = FakeMessage(headers={"Authorization": "Bearer example"})
    jetstream = FakeOrderedJetStream([message])

    result = await collect_ordered_inspection_records(
        jetstream,
        subject="orders.created",
        stream="ORDERS",
    )
    rendered = render_ordered_inspection_jsonl(result.records)
    parsed = json.loads(rendered)

    assert parsed["headers"]["Authorization"] == "<redacted>"
    assert parsed["payload"]["redacted"] is True
