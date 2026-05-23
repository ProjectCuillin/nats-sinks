# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for core message authenticity verification."""

from __future__ import annotations

import base64
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nats_sinks import NatsEnvelope
from nats_sinks.core.authenticity import (
    canonical_message_authenticity_bytes,
    evaluate_message_authenticity,
    hmac_sha256_signature_b64,
)
from nats_sinks.core.config import (
    DeadLetterConfig,
    MessageAuthenticityConfig,
    MessageAuthenticityRuleConfig,
    load_config,
    redacted_config,
)
from nats_sinks.core.errors import DeadLetterError
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames
from nats_sinks.core.runner import JetStreamSinkRunner


@dataclass
class FakeSequence:
    stream: int
    consumer: int


@dataclass
class FakeMetadata:
    stream: str = "ORDERS"
    consumer: str = "oracle"
    sequence: FakeSequence = field(default_factory=lambda: FakeSequence(stream=1, consumer=1))
    num_delivered: int = 1
    num_pending: int = 0


class FakeMessage:
    """Small raw-message double with authenticity headers."""

    def __init__(
        self,
        events: list[str],
        *,
        subject: str = "orders.created",
        data: bytes = b'{"order_id":"O-1001"}',
        headers: dict[str, str] | None = None,
        sequence: int = 1,
    ) -> None:
        self.subject = subject
        self.data = data
        self.headers = headers or {"Nats-Msg-Id": f"m-{sequence}"}
        self.metadata = FakeMetadata(sequence=FakeSequence(stream=sequence, consumer=sequence))
        self.events = events
        self.acked = False

    async def ack(self) -> None:
        self.events.append("ack")
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        del delay
        self.events.append("nak")


class RecordingSink:
    """Record the envelopes that reach sink delivery."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.messages: list[NatsEnvelope] = []

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.messages.extend(messages)
        self.events.append("write")
        self.events.append("commit")

    async def stop(self) -> None:
        return None


class FakeJetStream:
    """Minimal DLQ publisher used by runner tests."""

    def __init__(self, events: list[str], *, fail_publish: bool = False) -> None:
        self.events = events
        self.fail_publish = fail_publish
        self.published: list[tuple[str, bytes, dict[str, str] | None]] = []

    async def publish(
        self,
        subject: str,
        payload: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.events.append("dlq")
        if self.fail_publish:
            raise RuntimeError("DLQ unavailable")
        self.published.append((subject, payload, headers))


def _hmac_key() -> bytes:
    """Return generated HMAC key material for isolated tests."""

    return secrets.token_bytes(32)


def _key_b64(key: bytes) -> str:
    """Render binary verification material as base64 config text."""

    return base64.b64encode(key).decode("ascii")


def _auth_config(key: bytes, *, subject: str = ">") -> MessageAuthenticityConfig:
    """Return a minimal HMAC-SHA256 authenticity policy."""

    return MessageAuthenticityConfig(
        enabled=True,
        rules=[
            MessageAuthenticityRuleConfig(
                subject=subject,
                algorithm="hmac-sha256",
                key_id="unit-test-key",
                key_b64=_key_b64(key),
            )
        ],
    )


def _envelope(
    *,
    headers: dict[str, str],
    subject: str = "orders.created",
    data: bytes = b'{"order_id":"O-1001"}',
    sequence: int = 1,
) -> NatsEnvelope:
    """Build the normalized envelope used for signing and pure evaluation."""

    return NatsEnvelope(
        subject=subject,
        data=data,
        headers=headers,
        stream="ORDERS",
        consumer="oracle",
        stream_sequence=sequence,
        consumer_sequence=sequence,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


def _signed_headers(
    key: bytes,
    *,
    message_id: str = "m-1",
    subject: str = "orders.created",
    data: bytes = b'{"order_id":"O-1001"}',
    key_id: str = "unit-test-key",
) -> dict[str, str]:
    """Return headers with a valid HMAC-SHA256 message authenticity signature."""

    headers = {
        "Nats-Msg-Id": message_id,
        "Nats-Sinks-Authenticity-Algorithm": "hmac-sha256",
        "Nats-Sinks-Authenticity-Key-Id": key_id,
    }
    envelope = _envelope(headers=headers, subject=subject, data=data)
    headers["Nats-Sinks-Authenticity-Signature"] = hmac_sha256_signature_b64(
        envelope,
        key=key,
        key_id=key_id,
        signed_fields=("subject", "message_id"),
    )
    return headers


def test_hmac_signature_accepts_valid_message_and_rejects_tampered_payload() -> None:
    key = _hmac_key()
    config = _auth_config(key)
    headers = _signed_headers(key)

    accepted = evaluate_message_authenticity([_envelope(headers=headers)], config)
    rejected = evaluate_message_authenticity(
        [_envelope(headers=headers, data=b'{"order_id":"changed"}')],
        config,
    )

    assert accepted.accepted_indexes == (0,)
    assert accepted.rejected_indexes == ()
    assert rejected.accepted_indexes == ()
    assert rejected.rejected_indexes == (0,)
    assert rejected.violations[0].reason == "signature_invalid"


def test_ed25519_signature_accepts_valid_message() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    headers = {
        "Nats-Msg-Id": "m-1",
        "Nats-Sinks-Authenticity-Algorithm": "ed25519",
        "Nats-Sinks-Authenticity-Key-Id": "ed25519-unit-test",
    }
    envelope = _envelope(headers=headers)
    signature = private_key.sign(
        canonical_message_authenticity_bytes(
            envelope,
            algorithm="ed25519",
            key_id="ed25519-unit-test",
            signed_fields=("subject", "message_id"),
        )
    )
    headers["Nats-Sinks-Authenticity-Signature"] = base64.b64encode(signature).decode("ascii")
    config = MessageAuthenticityConfig(
        enabled=True,
        rules=[
            MessageAuthenticityRuleConfig(
                subject=">",
                algorithm="ed25519",
                key_id="ed25519-unit-test",
                key_b64=_key_b64(public_key),
            )
        ],
    )

    result = evaluate_message_authenticity([_envelope(headers=headers)], config)

    assert result.accepted_indexes == (0,)
    assert result.rejected_indexes == ()


def test_subject_rules_can_exempt_selected_subjects() -> None:
    key = _hmac_key()
    config = MessageAuthenticityConfig(
        enabled=True,
        rules=[
            MessageAuthenticityRuleConfig(subject="public.>", enabled=False),
            MessageAuthenticityRuleConfig(
                subject="secure.>",
                key_id="unit-test-key",
                key_b64=_key_b64(key),
            ),
        ],
    )

    result = evaluate_message_authenticity(
        [
            _envelope(subject="public.orders", headers={"Nats-Msg-Id": "public-1"}),
            _envelope(
                subject="secure.orders",
                headers=_signed_headers(key, subject="secure.orders", message_id="secure-1"),
            ),
        ],
        config,
    )

    assert result.accepted_indexes == (0, 1)
    assert result.rejected_indexes == ()


def test_missing_signature_and_unknown_key_id_are_rejected_safely() -> None:
    key = _hmac_key()
    config = _auth_config(key)
    missing = _envelope(
        headers={
            "Nats-Msg-Id": "m-1",
            "Nats-Sinks-Authenticity-Algorithm": "hmac-sha256",
            "Nats-Sinks-Authenticity-Key-Id": "unit-test-key",
        }
    )
    unknown_key = _envelope(headers=_signed_headers(key, key_id="unknown-key"))

    result = evaluate_message_authenticity([missing, unknown_key], config)

    assert result.accepted_indexes == ()
    assert result.rejected_indexes == (0, 1)
    assert [violation.reason for violation in result.violations] == [
        "signature_header_missing",
        "key_id_mismatch",
    ]


def test_malformed_and_oversized_signature_headers_are_rejected_safely() -> None:
    key = _hmac_key()
    config = _auth_config(key)
    malformed = _envelope(
        headers={
            "Nats-Msg-Id": "m-1",
            "Nats-Sinks-Authenticity-Algorithm": "hmac-sha256",
            "Nats-Sinks-Authenticity-Key-Id": "unit-test-key",
            "Nats-Sinks-Authenticity-Signature": "not base64!",
        }
    )
    oversized = _envelope(
        headers={
            "Nats-Msg-Id": "m-2",
            "Nats-Sinks-Authenticity-Algorithm": "hmac-sha256",
            "Nats-Sinks-Authenticity-Key-Id": "unit-test-key",
            "Nats-Sinks-Authenticity-Signature": "A" * 4096,
        }
    )

    result = evaluate_message_authenticity([malformed, oversized], config)

    assert result.accepted_indexes == ()
    assert result.rejected_indexes == (0, 1)
    assert [violation.reason for violation in result.violations] == [
        "signature_invalid",
        "signature_invalid",
    ]


def test_redacted_config_hides_direct_authenticity_key(tmp_path: Path) -> None:
    config_path = tmp_path / "auth-config.json"
    config_path.write_text(
        """
        {
          "nats": {
            "url": "nats://localhost:4222",
            "stream": "ORDERS",
            "consumer": "oracle",
            "subject": "orders.>"
          },
          "message_authenticity": {
            "enabled": true,
            "rules": [
              {
                "subject": ">",
                "algorithm": "hmac-sha256",
                "key_id": "unit-test-key",
                "key_b64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
              }
            ]
          },
          "sink": {
            "type": "file",
            "directory": "/tmp/nats-sinks-test",
            "fsync": false
          }
        }
        """,
        encoding="utf-8",
    )
    rendered = redacted_config(load_config(config_path))

    assert rendered["message_authenticity"]["rules"][0]["key_b64"] == "********"


@pytest.mark.asyncio
async def test_runner_verifies_before_sink_and_acks_after_commit() -> None:
    events: list[str] = []
    key = _hmac_key()
    sink = RecordingSink(events)
    message = FakeMessage(events, headers=_signed_headers(key))
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=sink,
        message_authenticity=_auth_config(key),
    )

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    assert message.acked
    assert sink.messages[0].data == message.data


@pytest.mark.asyncio
async def test_runner_invalid_signature_goes_to_dlq_before_ack_without_sink_write() -> None:
    events: list[str] = []
    key = _hmac_key()
    headers = _signed_headers(key)
    headers["Nats-Sinks-Authenticity-Signature"] = base64.b64encode(b"bad").decode("ascii")
    message = FakeMessage(events, headers=headers)
    metrics = InMemoryMetrics()
    js = FakeJetStream(events)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        message_authenticity=_auth_config(key),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=js,
        metrics=metrics,
    )

    await runner.process_raw_batch([message])

    assert events == ["dlq", "ack"]
    assert message.acked
    assert js.published[0][0] == "orders.dlq"
    assert metrics.counters[MetricNames.MESSAGE_AUTHENTICITY_MESSAGES_REJECTED_TOTAL] == 1
    assert metrics.counters[MetricNames.SINK_WRITE_ERRORS_TOTAL] == 0


@pytest.mark.asyncio
async def test_runner_authenticity_dlq_failure_does_not_ack_or_write() -> None:
    events: list[str] = []
    key = _hmac_key()
    headers = _signed_headers(key)
    headers["Nats-Sinks-Authenticity-Signature"] = base64.b64encode(b"bad").decode("ascii")
    message = FakeMessage(events, headers=headers)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=RecordingSink(events),
        message_authenticity=_auth_config(key),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events, fail_publish=True),
    )

    with pytest.raises(DeadLetterError):
        await runner.process_raw_batch([message])

    assert events == ["dlq"]
    assert not message.acked


@pytest.mark.asyncio
async def test_runner_mixed_authenticity_batch_dlqs_rejected_and_writes_accepted() -> None:
    events: list[str] = []
    key = _hmac_key()
    accepted = FakeMessage(events, headers=_signed_headers(key), sequence=1)
    bad_headers = _signed_headers(key, message_id="m-2")
    bad_headers["Nats-Sinks-Authenticity-Signature"] = base64.b64encode(b"bad").decode("ascii")
    rejected = FakeMessage(events, headers=bad_headers, sequence=2)
    sink = RecordingSink(events)
    metrics = InMemoryMetrics()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="oracle",
        subject="orders.*",
        sink=sink,
        message_authenticity=_auth_config(key),
        dead_letter=DeadLetterConfig(enabled=True, subject="orders.dlq"),
        jetstream=FakeJetStream(events),
        metrics=metrics,
    )

    await runner.process_raw_batch([accepted, rejected])

    assert events == ["dlq", "ack", "write", "commit", "ack"]
    assert accepted.acked
    assert rejected.acked
    assert [message.stream_sequence for message in sink.messages] == [1]
    assert metrics.counters[MetricNames.MESSAGE_AUTHENTICITY_MESSAGES_PASSED_TOTAL] == 1
    assert metrics.counters[MetricNames.MESSAGE_AUTHENTICITY_MESSAGES_REJECTED_TOTAL] == 1
