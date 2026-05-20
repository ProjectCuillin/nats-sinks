# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from nats_sinks import NatsEnvelope
from nats_sinks.core.config import (
    EncryptionConfig,
    EncryptionRuleConfig,
    load_config,
    redacted_config,
)
from nats_sinks.core.encryption import ENCRYPTED_PAYLOAD_KEY, PayloadEncryptor
from nats_sinks.core.errors import ConfigurationError, SerializationError
from nats_sinks.core.runner import JetStreamSinkRunner


def _key_b64() -> str:
    """Return generated AES-256 key material for deterministic test isolation."""

    configured = os.getenv("NATS_SINKS_TEST_ENCRYPTION_KEY_B64")
    if configured:
        return configured
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _encryption_config(*, algorithm: str = "aes-256-gcm") -> EncryptionConfig:
    return EncryptionConfig(
        enabled=True,
        algorithm=algorithm,
        key_id="unit-test-key",
        key_b64=_key_b64(),
    )


def _envelope(*, subject: str, data: bytes = b'{"order_id":"O-1001"}') -> NatsEnvelope:
    """Return a minimal normalized envelope for subject-encryption tests."""

    return NatsEnvelope(
        subject=subject,
        data=data,
        headers={},
        stream="ORDERS",
        consumer="file-orders-sink",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


def _load_payload(value: bytes) -> dict[str, object]:
    loaded = json.loads(value.decode("utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_aes_256_gcm_encrypts_and_decrypts_payload_bytes() -> None:
    config = _encryption_config(algorithm="AES-256-GCM")
    encryptor = PayloadEncryptor(config)

    encrypted = encryptor.encrypt_bytes(b'{"order_id":"O-1001"}')
    encrypted_payload = _load_payload(encrypted)
    decrypted = encryptor.decrypt_payload(encrypted_payload)

    assert decrypted == b'{"order_id":"O-1001"}'
    assert b"O-1001" not in encrypted
    assert encrypted_payload[ENCRYPTED_PAYLOAD_KEY]["algorithm"] == "aes-256-gcm"  # type: ignore[index]


def test_aes_256_ccm_encrypts_and_decrypts_payload_bytes() -> None:
    config = _encryption_config(algorithm="aes-256-ccm")
    encryptor = PayloadEncryptor(config)

    encrypted = encryptor.encrypt_bytes(b"encrypted-text-that-was-plain-before-core-encryption")
    decrypted = encryptor.decrypt_payload(encrypted)

    assert decrypted == b"encrypted-text-that-was-plain-before-core-encryption"
    assert b"plain-before-core-encryption" not in encrypted


def test_empty_payload_encrypts_and_decrypts_without_special_handling() -> None:
    config = _encryption_config()
    encryptor = PayloadEncryptor(config)

    encrypted = encryptor.encrypt_bytes(b"")

    assert encryptor.decrypt_payload(encrypted) == b""
    encrypted_metadata = _load_payload(encrypted)[ENCRYPTED_PAYLOAD_KEY]
    assert encrypted_metadata["plaintext_size_bytes"] == 0  # type: ignore[index]


def test_encryption_requires_256_bit_key_material() -> None:
    with pytest.raises(ConfigurationError, match="32 bytes"):
        EncryptionConfig(
            enabled=True,
            algorithm="aes-256-gcm",
            key_id="bad-key",
            key_b64=base64.b64encode(b"too-short").decode("ascii"),
        )


def test_encryption_requires_key_source_when_enabled() -> None:
    with pytest.raises(ValueError, match=r"key_b64_env or encryption\.key_b64"):
        EncryptionConfig(enabled=True)


def test_subject_rule_encrypts_only_matching_subjects() -> None:
    key_b64 = _key_b64()
    config = EncryptionConfig(
        enabled=False,
        rules=[
            EncryptionRuleConfig(
                subject="secure.>",
                enabled=True,
                key_id="secure-subject-key",
                key_b64=key_b64,
            )
        ],
    )
    transformer = PayloadEncryptor.from_config(config)
    assert transformer is not None

    secure, public = transformer.encrypt_batch(
        [
            _envelope(subject="secure.orders.created", data=b"secret"),
            _envelope(subject="public.orders.created", data=b"public"),
        ]
    )

    rule_encryptor = PayloadEncryptor(config.effective_rule_config(config.rules[0]))
    assert ENCRYPTED_PAYLOAD_KEY in _load_payload(secure.data)
    assert rule_encryptor.decrypt_payload(secure.data) == b"secret"
    assert public.data == b"public"


def test_subject_rule_can_disable_global_encryption() -> None:
    config = EncryptionConfig(
        enabled=True,
        key_id="global-key",
        key_b64=_key_b64(),
        rules=[EncryptionRuleConfig(subject="public.>", enabled=False)],
    )
    transformer = PayloadEncryptor.from_config(config)
    assert transformer is not None

    public, private = transformer.encrypt_batch(
        [
            _envelope(subject="public.orders.created", data=b"visible"),
            _envelope(subject="private.orders.created", data=b"hidden"),
        ]
    )

    assert public.data == b"visible"
    assert PayloadEncryptor(config).decrypt_payload(private.data) == b"hidden"


def test_first_matching_subject_rule_wins() -> None:
    config = EncryptionConfig(
        enabled=False,
        rules=[
            EncryptionRuleConfig(subject="secure.>", enabled=False),
            EncryptionRuleConfig(
                subject="secure.orders",
                enabled=True,
                key_id="should-not-be-used",
                key_b64=_key_b64(),
            ),
        ],
    )
    transformer = PayloadEncryptor.from_config(config)
    assert transformer is not None

    encrypted = transformer.encrypt_batch([_envelope(subject="secure.orders", data=b"first")])[0]

    assert encrypted.data == b"first"


def test_subject_rules_support_distinct_key_identifiers() -> None:
    first_key = _key_b64()
    second_key = _key_b64()
    config = EncryptionConfig(
        enabled=False,
        rules=[
            EncryptionRuleConfig(
                subject="orders.secure",
                enabled=True,
                key_id="orders-key",
                key_b64=first_key,
            ),
            EncryptionRuleConfig(
                subject="payments.secure",
                enabled=True,
                key_id="payments-key",
                key_b64=second_key,
            ),
        ],
    )
    transformer = PayloadEncryptor.from_config(config)
    assert transformer is not None

    orders, payments = transformer.encrypt_batch(
        [
            _envelope(subject="orders.secure", data=b"orders"),
            _envelope(subject="payments.secure", data=b"payments"),
        ]
    )

    orders_payload = _load_payload(orders.data)[ENCRYPTED_PAYLOAD_KEY]
    payments_payload = _load_payload(payments.data)[ENCRYPTED_PAYLOAD_KEY]
    assert orders_payload["key_id"] == "orders-key"  # type: ignore[index]
    assert payments_payload["key_id"] == "payments-key"  # type: ignore[index]
    assert (
        PayloadEncryptor(config.effective_rule_config(config.rules[0])).decrypt_payload(orders.data)
        == b"orders"
    )
    assert (
        PayloadEncryptor(config.effective_rule_config(config.rules[1])).decrypt_payload(
            payments.data
        )
        == b"payments"
    )


def test_enabled_subject_rule_requires_key_source_when_global_key_is_absent() -> None:
    with pytest.raises(ValueError, match=r"encryption\.rules\[0\].*required"):
        EncryptionConfig(
            enabled=False,
            rules=[EncryptionRuleConfig(subject="secure.>", enabled=True)],
        )


def test_subject_rule_rejects_invalid_subject_pattern() -> None:
    with pytest.raises(ConfigurationError, match="invalid NATS subject pattern"):
        EncryptionRuleConfig(subject="orders..created", enabled=True, key_b64=_key_b64())

    with pytest.raises(ConfigurationError, match="final token"):
        EncryptionRuleConfig(subject="orders.>.created", enabled=True, key_b64=_key_b64())


def test_redacted_config_hides_direct_encryption_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "encryption": {
                    "enabled": True,
                    "algorithm": "aes-256-gcm",
                    "key_id": "redaction-test",
                    "key_b64": _key_b64(),
                },
                "sink": {
                    "type": "file",
                    "directory": ".local/file-sink/events",
                },
            }
        ),
        encoding="utf-8",
    )

    rendered = redacted_config(load_config(path, env_overrides=False))

    assert rendered["encryption"]["key_b64"] == "********"
    assert rendered["encryption"]["key_id"] == "redaction-test"


def test_decrypt_rejects_wrong_key_id() -> None:
    config = _encryption_config()
    encrypted = PayloadEncryptor(config).encrypt_bytes(b"important data")
    wrong_config = EncryptionConfig(
        enabled=True,
        algorithm=config.algorithm,
        key_id="different-key-id",
        key_b64=config.key_b64,
    )

    with pytest.raises(SerializationError, match="key_id"):
        PayloadEncryptor(wrong_config).decrypt_payload(encrypted)


@dataclass
class FakeSequence:
    stream: int
    consumer: int


@dataclass
class FakeMetadata:
    stream: str = "ORDERS"
    consumer: str = "file-orders-sink"
    sequence: FakeSequence = field(default_factory=lambda: FakeSequence(stream=1, consumer=1))
    num_delivered: int = 1
    num_pending: int = 0


class FakeMessage:
    """Small raw-message double with the fields the runner normalizes."""

    def __init__(
        self,
        events: list[str],
        *,
        subject: str = "orders.created",
        data: bytes = b'{"order_id":"O-1001"}',
    ) -> None:
        self.subject = subject
        self.data = data
        self.headers = {"Nats-Msg-Id": "m-1"}
        self.metadata = FakeMetadata()
        self.events = events
        self.acked = False

    async def ack(self) -> None:
        self.events.append("ack")
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        del delay
        self.events.append("nak")


class RecordingSink:
    """Record the envelope seen by a sink after core encryption."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.messages: list[NatsEnvelope] = []

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.events.append("write")
        self.messages.extend(messages)
        self.events.append("commit")

    async def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_runner_encrypts_payload_before_sink_and_acks_after_commit() -> None:
    events: list[str] = []
    sink = RecordingSink(events)
    config = _encryption_config()
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="file-orders-sink",
        subject="orders.*",
        sink=sink,
        encryption=config,
    )
    message = FakeMessage(events)

    await runner.process_raw_batch([message])

    assert events == ["write", "commit", "ack"]
    encrypted_envelope = sink.messages[0]
    assert encrypted_envelope.subject == "orders.created"
    assert encrypted_envelope.headers["Nats-Msg-Id"] == "m-1"
    assert encrypted_envelope.data != message.data
    assert PayloadEncryptor(config).decrypt_payload(encrypted_envelope.data) == message.data
    assert message.acked


@pytest.mark.asyncio
async def test_runner_applies_subject_encryption_rules_before_sink() -> None:
    events: list[str] = []
    sink = RecordingSink(events)
    config = EncryptionConfig(
        enabled=False,
        rules=[
            EncryptionRuleConfig(
                subject="secure.>",
                enabled=True,
                key_id="secure-runner-key",
                key_b64=_key_b64(),
            )
        ],
    )
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="file-orders-sink",
        subject=">",
        sink=sink,
        encryption=config,
    )
    secure_message = FakeMessage(events, subject="secure.orders", data=b"secret")
    public_message = FakeMessage(events, subject="public.orders", data=b"public")

    await runner.process_raw_batch([secure_message, public_message])

    assert events == ["write", "commit", "ack", "ack"]
    secure_envelope, public_envelope = sink.messages
    assert (
        PayloadEncryptor(config.effective_rule_config(config.rules[0])).decrypt_payload(
            secure_envelope.data
        )
        == b"secret"
    )
    assert public_envelope.data == b"public"
    assert secure_message.acked
    assert public_message.acked
