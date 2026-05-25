# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Framework-level payload encryption.

This module encrypts message bodies before they are handed to a destination
sink.  The design is intentionally sink-neutral: Oracle, file, and future sinks
all receive the same immutable `NatsEnvelope` shape, with only the `data` bytes
replaced by a JSON encryption envelope.

Only the original NATS message body is encrypted.  Operational metadata such as
subject, headers, JetStream stream name, stream sequence, and timestamps remains
available in clear text so sinks can route messages, apply idempotency keys, and
produce useful audit records.  The encrypted payload envelope stores base64
ciphertext and nonce values inside JSON so existing JSON-capable sink storage
paths do not need destination-specific binary handling.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol, cast

from nats_sinks.core.config import EncryptionConfig
from nats_sinks.core.errors import ConfigurationError, SerializationError
from nats_sinks.core.subjects import matches_subject

if TYPE_CHECKING:
    from nats_sinks.core.envelope import NatsEnvelope

ENCRYPTED_PAYLOAD_KEY = "_nats_sinks_encryption"
ENCRYPTED_PAYLOAD_SCHEMA = "nats_sinks.encrypted_payload.v1"
ENCRYPTED_PAYLOAD_VERSION = 1


def _b64_encode(value: bytes) -> str:
    """Encode binary values in a stable ASCII representation for JSON."""

    return base64.b64encode(value).decode("ascii")


def _b64_decode(value: object, *, field: str) -> bytes:
    """Decode an encrypted payload field and report safe, non-secret errors."""

    if not isinstance(value, str):
        raise SerializationError(f"encrypted payload field {field} must be a base64 string")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise SerializationError(f"encrypted payload field {field} is not valid base64") from exc


def _load_aead_classes() -> tuple[type[Any], type[Any]]:
    """Import optional AEAD implementations only when encryption is enabled."""

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESCCM, AESGCM  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on optional extra installation.
        raise ConfigurationError(
            "payload encryption requires the optional crypto extra: "
            'pip install "nats-sinks[crypto]"'
        ) from exc
    return AESGCM, AESCCM


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize JSON envelopes consistently for storage and tests."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _reject_duplicate_json_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous encryption envelope JSON objects."""

    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = item
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject Python-only constants in encrypted payload envelopes."""

    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _load_encrypted_payload(value: bytes | str | Mapping[str, Any]) -> Mapping[str, Any]:
    """Load the encrypted payload envelope from bytes, text, or a parsed mapping."""

    if isinstance(value, Mapping):
        payload = value
    else:
        try:
            text = value.decode("utf-8") if isinstance(value, bytes) else value
            loaded = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_json_object_keys,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SerializationError("encrypted payload is not a valid JSON envelope") from exc
        except ValueError as exc:
            raise SerializationError("encrypted payload is not a valid JSON envelope") from exc
        if not isinstance(loaded, Mapping):
            raise SerializationError("encrypted payload root must be a JSON object")
        payload = loaded

    envelope = payload.get(ENCRYPTED_PAYLOAD_KEY)
    if not isinstance(envelope, Mapping):
        raise SerializationError("payload does not contain a nats-sinks encryption envelope")
    return envelope


def is_encrypted_payload_envelope(value: bytes | str | Mapping[str, Any]) -> bool:
    """Return whether a value is a nats-sinks encrypted payload envelope.

    Policy checks use this helper to verify that a message body has already
    passed through core payload encryption before it reaches a sink. The helper
    intentionally returns `False` for malformed input rather than exposing
    parser errors or payload content in policy messages.
    """

    try:
        envelope = _load_encrypted_payload(value)
    except SerializationError:
        return False
    return (
        envelope.get("schema") == ENCRYPTED_PAYLOAD_SCHEMA
        and envelope.get("version") == ENCRYPTED_PAYLOAD_VERSION
        and isinstance(envelope.get("algorithm"), str)
        and isinstance(envelope.get("key_id"), str)
        and isinstance(envelope.get("ciphertext"), str)
    )


class PayloadTransformer(Protocol):
    """Small runtime protocol for objects that transform envelope payloads.

    The runner only needs one operation: turn a batch of normalized envelopes
    into the batch that should be delivered to the sink.  A single global
    `PayloadEncryptor` and the subject-aware wrapper below both satisfy this
    protocol, which keeps the runner independent from policy details.
    """

    def encrypt_batch(self, envelopes: Sequence[NatsEnvelope]) -> list[NatsEnvelope]:
        """Return the envelopes to pass to the sink."""
        ...


class PayloadEncryptor:
    """Encrypt and decrypt `NatsEnvelope.data` using an AEAD algorithm.

    The encryptor is created by the core runner when `encryption.enabled` is
    true.  It keeps the resolved key in memory for the lifetime of the runner
    and never logs or exposes that key.  Each message uses a fresh nonce from
    Python's `secrets` module, which makes ciphertext non-deterministic even
    when the same plaintext is redelivered.
    """

    def __init__(self, config: EncryptionConfig) -> None:
        self.config = config
        self._key = config.resolve_key()
        self._aesgcm_class, self._aesccm_class = _load_aead_classes()

    @classmethod
    def from_config(cls, config: EncryptionConfig) -> PayloadTransformer | None:
        """Return an encryptor for global or subject-specific policies.

        A config without `rules` keeps the original behavior: `enabled=true`
        encrypts every subject and `enabled=false` leaves all messages unchanged.
        When rules are present, the subject-aware wrapper evaluates ordered
        first-match rules and uses this class for each concrete encryption
        policy.
        """

        if config.rules:
            if config.enabled or any(rule.enabled for rule in config.rules):
                return SubjectPayloadEncryptor(config)
            return None
        if not config.enabled:
            return None
        return cls(config)

    def encrypt_batch(self, envelopes: Sequence[NatsEnvelope]) -> list[NatsEnvelope]:
        """Encrypt a batch while preserving each envelope's metadata."""

        return [self.encrypt_envelope(envelope) for envelope in envelopes]

    def encrypt_envelope(self, envelope: NatsEnvelope) -> NatsEnvelope:
        """Return a copy of `envelope` with encrypted payload bytes."""

        encrypted = self.encrypt_bytes(envelope.data)
        return replace(envelope, data=encrypted)

    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        """Encrypt raw payload bytes and return a JSON encryption envelope."""

        nonce = secrets.token_bytes(self.config.nonce_size_bytes)
        if self.config.algorithm == "aes-256-gcm":
            cipher = self._aesgcm_class(self._key)
            ciphertext = cipher.encrypt(nonce, plaintext, None)
            tag_length = 16
        else:
            cipher = self._aesccm_class(self._key, tag_length=self.config.tag_length)
            ciphertext = cipher.encrypt(nonce, plaintext, None)
            tag_length = self.config.tag_length

        payload = {
            ENCRYPTED_PAYLOAD_KEY: {
                "schema": ENCRYPTED_PAYLOAD_SCHEMA,
                "version": ENCRYPTED_PAYLOAD_VERSION,
                "algorithm": self.config.algorithm,
                "key_id": self.config.key_id,
                "nonce": _b64_encode(nonce),
                "nonce_size_bytes": len(nonce),
                "ciphertext": _b64_encode(ciphertext),
                "ciphertext_encoding": "base64",
                "tag_length": tag_length,
                "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
                "plaintext_size_bytes": len(plaintext),
            }
        }
        return _canonical_json_bytes(payload)

    def decrypt_payload(self, value: bytes | str | Mapping[str, Any]) -> bytes:
        """Decrypt an encrypted payload envelope and verify integrity metadata."""

        encrypted = _load_encrypted_payload(value)
        algorithm = encrypted.get("algorithm")
        if algorithm != self.config.algorithm:
            raise SerializationError("encrypted payload algorithm does not match configuration")
        if encrypted.get("key_id") != self.config.key_id:
            raise SerializationError("encrypted payload key_id does not match configuration")
        if encrypted.get("schema") != ENCRYPTED_PAYLOAD_SCHEMA:
            raise SerializationError("encrypted payload schema is not supported")
        if encrypted.get("version") != ENCRYPTED_PAYLOAD_VERSION:
            raise SerializationError("encrypted payload version is not supported")

        nonce = _b64_decode(encrypted.get("nonce"), field="nonce")
        ciphertext = _b64_decode(encrypted.get("ciphertext"), field="ciphertext")
        if algorithm == "aes-256-gcm":
            cipher = self._aesgcm_class(self._key)
        else:
            tag_length = encrypted.get("tag_length")
            if not isinstance(tag_length, int):
                raise SerializationError("encrypted payload tag_length must be an integer")
            cipher = self._aesccm_class(self._key, tag_length=tag_length)

        try:
            plaintext = cast("bytes", cipher.decrypt(nonce, ciphertext, None))
        except Exception as exc:
            raise SerializationError("encrypted payload could not be decrypted") from exc

        expected_size = encrypted.get("plaintext_size_bytes")
        if expected_size is not None and expected_size != len(plaintext):
            raise SerializationError("encrypted payload plaintext size check failed")
        expected_digest = encrypted.get("plaintext_sha256")
        if expected_digest is not None and expected_digest != hashlib.sha256(plaintext).hexdigest():
            raise SerializationError("encrypted payload plaintext digest check failed")
        return plaintext


class PayloadKeyRegistry:
    """Decrypt encrypted payload envelopes during key-rotation windows.

    The runtime encryptor writes a non-secret `key_id` into every encrypted
    payload envelope.  Operators can keep old and new decryption keys available
    to offline verification, replay, migration, or incident-response tooling by
    registering one `EncryptionConfig` per key identifier.  The registry never
    changes the encrypted envelope shape and it is not part of ACK decisions.

    Key material is resolved by the normal `EncryptionConfig` path, so callers
    can use direct test keys, environment variables populated by a deployment
    platform, or values fetched from a secret manager by their own bootstrap
    code.  No cloud-provider SDK is imported here; that keeps nats-sinks small
    and avoids turning every installation into every secret-manager client.
    """

    def __init__(self, configs: Sequence[EncryptionConfig]) -> None:
        if not configs:
            raise ConfigurationError("payload key registry requires at least one key config")

        encryptors: dict[str, PayloadEncryptor] = {}
        for config in configs:
            key_id = config.key_id
            if key_id in encryptors:
                raise ConfigurationError(f"duplicate payload encryption key_id: {key_id}")
            encryptors[key_id] = PayloadEncryptor(config)
        self._encryptors = encryptors

    @property
    def key_ids(self) -> tuple[str, ...]:
        """Return registered non-secret key identifiers in deterministic order."""

        return tuple(sorted(self._encryptors))

    def decrypt_payload(self, value: bytes | str | Mapping[str, Any]) -> bytes:
        """Decrypt an encrypted payload by selecting the matching `key_id`.

        Unknown or malformed key identifiers fail closed with a framework
        serialization error.  Error messages intentionally mention only the
        key identifier contract, never key material, environment variables, or
        backend secret-store locations.
        """

        encrypted = _load_encrypted_payload(value)
        key_id = encrypted.get("key_id")
        if not isinstance(key_id, str) or not key_id:
            raise SerializationError("encrypted payload key_id is missing or invalid")
        encryptor = self._encryptors.get(key_id)
        if encryptor is None:
            raise SerializationError("encrypted payload key_id is not registered")
        return encryptor.decrypt_payload({ENCRYPTED_PAYLOAD_KEY: encrypted})


class SubjectPayloadEncryptor:
    """Apply payload encryption according to ordered NATS subject rules.

    Rules are evaluated in configuration order and the first matching subject
    pattern wins.  A matching disabled rule returns the original envelope, which
    lets operators carve out public or already-encrypted subjects from a global
    encryption policy.  If no rule matches, the top-level `enabled` setting is
    used as the fallback.
    """

    def __init__(self, config: EncryptionConfig) -> None:
        self.config = config
        self._default_encryptor = PayloadEncryptor(config) if config.enabled else None
        self._rules: list[tuple[str, PayloadEncryptor | None]] = []
        for rule in config.rules:
            encryptor = (
                PayloadEncryptor(config.effective_rule_config(rule)) if rule.enabled else None
            )
            self._rules.append((rule.subject, encryptor))

    def encrypt_batch(self, envelopes: Sequence[NatsEnvelope]) -> list[NatsEnvelope]:
        """Encrypt each envelope with the first matching subject policy."""

        return [self.encrypt_envelope(envelope) for envelope in envelopes]

    def encrypt_envelope(self, envelope: NatsEnvelope) -> NatsEnvelope:
        """Return the encrypted or unchanged envelope for one subject."""

        encryptor = self._encryptor_for_subject(envelope.subject)
        if encryptor is None:
            return envelope
        return encryptor.encrypt_envelope(envelope)

    def _encryptor_for_subject(self, subject: str) -> PayloadEncryptor | None:
        """Resolve the first matching rule before applying the global fallback."""

        for pattern, encryptor in self._rules:
            if matches_subject(pattern, subject):
                return encryptor
        return self._default_encryptor


def decrypt_payload(
    value: bytes | str | Mapping[str, Any],
    *,
    config: EncryptionConfig,
) -> bytes:
    """Convenience helper for tests and operational verification tools."""

    return PayloadEncryptor(config).decrypt_payload(value)
