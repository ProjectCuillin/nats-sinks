# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Message-level authenticity verification before sink delivery.

NATS authentication and TLS protect the connection to the broker.  They do not
prove that an individual message body was produced by an authorized publisher,
especially in environments where internal systems, service accounts, or
producers can be compromised.  This module adds an optional destination-neutral
verification gate in the core runtime.

The verifier is deliberately small and reviewable.  Operators configure
subject-specific allow-listed algorithms and key identifiers.  Publishers place
the algorithm, key identifier, and base64 signature in NATS headers.  The core
builds a deterministic canonical JSON document from the payload hash and
selected normalized metadata fields, verifies the signature, and rejects failed
messages before any sink sees them.

Verification failure is a permanent pre-sink validation failure.  The runner
therefore uses the same DLQ-before-ACK behavior as other fail-closed core
policies: the original JetStream message is acknowledged only after DLQ
publication succeeds, or after the sink commits for messages that pass.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nats_sinks.core.errors import ConfigurationError, PolicyViolationError
from nats_sinks.core.message_metadata import case_insensitive_header
from nats_sinks.core.subjects import matches_subject

if TYPE_CHECKING:
    from nats_sinks.core.config import (
        MessageAuthenticityConfig,
        MessageAuthenticityRuleConfig,
    )
    from nats_sinks.core.envelope import NatsEnvelope

MESSAGE_AUTHENTICITY_SCHEMA = "nats_sinks.message_authenticity.v1"
MESSAGE_AUTHENTICITY_VERSION = 1
SUPPORTED_MESSAGE_AUTHENTICITY_ALGORITHMS = frozenset({"hmac-sha256", "ed25519"})
MAX_SIGNATURE_BYTES = 1024
MAX_SIGNATURE_BASE64_CHARS = 4 * ((MAX_SIGNATURE_BYTES + 2) // 3)


@dataclass(frozen=True, slots=True)
class MessageAuthenticityViolation:
    """One sanitized authenticity failure.

    The violation intentionally stores only stable reason codes, subject names,
    and configured rule subjects. It never stores signature material, key
    material, payload bytes, or header values.
    """

    index: int
    subject: str
    rule_subject: str
    reason: str


@dataclass(frozen=True, slots=True)
class MessageAuthenticityEvaluation:
    """Result of evaluating a batch against authenticity verification rules."""

    accepted_indexes: tuple[int, ...]
    rejected_indexes: tuple[int, ...]
    violations: tuple[MessageAuthenticityViolation, ...]

    @property
    def has_rejections(self) -> bool:
        """Return whether at least one message failed verification."""

        return bool(self.rejected_indexes)


@dataclass(frozen=True, slots=True)
class _PreparedAuthenticityRule:
    """Runtime representation of one configured authenticity rule."""

    config: MessageAuthenticityRuleConfig
    key: bytes | None


def canonical_message_authenticity_document(
    envelope: NatsEnvelope,
    *,
    algorithm: str,
    key_id: str,
    signed_fields: Sequence[str],
) -> dict[str, Any]:
    """Return the canonical document that producers sign and consumers verify.

    The payload itself is never copied into this document.  Instead, the
    canonical record contains a SHA-256 payload hash plus the configured
    normalized metadata fields.  This keeps signing input deterministic and
    bounded while still binding the signature to the exact message body.
    """

    return {
        "schema": MESSAGE_AUTHENTICITY_SCHEMA,
        "version": MESSAGE_AUTHENTICITY_VERSION,
        "algorithm": algorithm,
        "key_id": key_id,
        "payload_sha256": hashlib.sha256(envelope.data).hexdigest(),
        "metadata": _selected_metadata(envelope, signed_fields=signed_fields),
    }


def canonical_message_authenticity_bytes(
    envelope: NatsEnvelope,
    *,
    algorithm: str,
    key_id: str,
    signed_fields: Sequence[str],
) -> bytes:
    """Return deterministic UTF-8 JSON bytes for message signature checks."""

    try:
        rendered = json.dumps(
            canonical_message_authenticity_document(
                envelope,
                algorithm=algorithm,
                key_id=key_id,
                signed_fields=signed_fields,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PolicyViolationError("message authenticity canonical content is invalid") from exc
    return rendered.encode("utf-8")


def hmac_sha256_signature_b64(
    envelope: NatsEnvelope,
    *,
    key: bytes,
    key_id: str,
    signed_fields: Sequence[str],
) -> str:
    """Create a base64 HMAC-SHA256 signature for tests and producer examples."""

    material = canonical_message_authenticity_bytes(
        envelope,
        algorithm="hmac-sha256",
        key_id=key_id,
        signed_fields=signed_fields,
    )
    return base64.b64encode(hmac.new(key, material, hashlib.sha256).digest()).decode("ascii")


def message_authenticity_violation_error(
    violations: Sequence[MessageAuthenticityViolation],
) -> PolicyViolationError:
    """Build one safe framework error for authenticity rejections."""

    if not violations:
        return PolicyViolationError("message authenticity verification failed")
    first = violations[0]
    rejected_count = len({violation.index for violation in violations})
    return PolicyViolationError(
        f"message authenticity verification rejected {rejected_count} message(s); "
        f"first subject={first.subject!r} rule={first.rule_subject!r} reason={first.reason}"
    )


def evaluate_message_authenticity(
    envelopes: Sequence[NatsEnvelope],
    config: MessageAuthenticityConfig,
) -> MessageAuthenticityEvaluation:
    """Evaluate a batch using freshly resolved configured verification keys."""

    authenticator = MessageAuthenticator.from_config(config)
    if authenticator is None:
        return MessageAuthenticityEvaluation(
            accepted_indexes=tuple(range(len(envelopes))),
            rejected_indexes=(),
            violations=(),
        )
    return authenticator.evaluate(envelopes)


class MessageAuthenticator:
    """Prepared message authenticity verifier used by the runner."""

    def __init__(self, config: MessageAuthenticityConfig) -> None:
        self.config = config
        self.rules = tuple(_prepare_rule(rule) for rule in config.rules)

    @classmethod
    def from_config(cls, config: MessageAuthenticityConfig) -> MessageAuthenticator | None:
        """Return a prepared authenticator, or `None` when disabled."""

        if not config.enabled:
            return None
        return cls(config)

    def evaluate(self, envelopes: Sequence[NatsEnvelope]) -> MessageAuthenticityEvaluation:
        """Evaluate one batch against the prepared authenticity rules."""

        accepted: list[int] = []
        rejected: list[int] = []
        violations: list[MessageAuthenticityViolation] = []

        for index, envelope in enumerate(envelopes):
            violation = self._violation_for_envelope(index, envelope)
            if violation is None:
                accepted.append(index)
            else:
                rejected.append(index)
                violations.append(violation)

        return MessageAuthenticityEvaluation(
            accepted_indexes=tuple(accepted),
            rejected_indexes=tuple(rejected),
            violations=tuple(violations),
        )

    def _violation_for_envelope(
        self,
        index: int,
        envelope: NatsEnvelope,
    ) -> MessageAuthenticityViolation | None:
        """Return a sanitized violation when one envelope fails verification."""

        prepared_rule = next(
            (rule for rule in self.rules if matches_subject(rule.config.subject, envelope.subject)),
            None,
        )
        if prepared_rule is None:
            if self.config.unmatched_subject_action == "reject":
                return _violation(
                    index,
                    envelope,
                    "<unmatched>",
                    "subject_not_covered_by_authenticity_policy",
                )
            return None

        rule = prepared_rule.config
        if not rule.enabled:
            return None
        if prepared_rule.key is None:
            raise ConfigurationError("message_authenticity enabled rule has no verification key")
        if rule.key_id is None:
            raise ConfigurationError("message_authenticity enabled rule has no key identifier")

        failure_reason = _authenticity_failure_reason(
            envelope=envelope,
            config=self.config,
            rule=rule,
            key=prepared_rule.key,
        )

        if failure_reason is not None:
            return _violation(index, envelope, rule.subject, failure_reason)
        return None


def _prepare_rule(rule: MessageAuthenticityRuleConfig) -> _PreparedAuthenticityRule:
    """Resolve verification key material for one enabled rule."""

    key = rule.resolve_key() if rule.enabled else None
    if rule.enabled and rule.algorithm == "ed25519" and key is not None:
        _validate_ed25519_public_key(key)
    return _PreparedAuthenticityRule(
        config=rule,
        key=key,
    )


def _selected_metadata(envelope: NatsEnvelope, *, signed_fields: Sequence[str]) -> dict[str, Any]:
    """Return the configured metadata fields for canonical signature input."""

    metadata: dict[str, Any] = {}
    for field_name in signed_fields:
        if field_name == "subject":
            metadata["subject"] = envelope.subject
        elif field_name == "message_id":
            metadata["message_id"] = envelope.message_id
        elif field_name == "priority":
            metadata["priority"] = envelope.priority
        elif field_name == "classification":
            metadata["classification"] = envelope.classification
        elif field_name == "labels":
            metadata["labels"] = list(envelope.labels)
        elif field_name == "mission_metadata":
            metadata["mission_metadata"] = envelope.mission_metadata_for_json_storage()
        elif field_name == "security_labels":
            metadata["security_labels"] = envelope.security_labels_for_json_storage()
        else:
            raise PolicyViolationError("message authenticity signed field is not supported")
    return metadata


def _authenticity_failure_reason(
    *,
    envelope: NatsEnvelope,
    config: MessageAuthenticityConfig,
    rule: MessageAuthenticityRuleConfig,
    key: bytes,
) -> str | None:
    """Return a sanitized reason code when one signed envelope fails checks."""

    reason: str | None = None
    algorithm = _normalized_header(
        envelope.headers,
        config.algorithm_header,
        normalize_algorithm=True,
    )
    key_id = _normalized_header(envelope.headers, config.key_id_header)
    signature = _signature_bytes(envelope.headers, config.signature_header)

    if algorithm is None:
        reason = "algorithm_header_missing"
    elif algorithm != rule.algorithm:
        reason = "algorithm_mismatch"
    elif key_id is None:
        reason = "key_id_header_missing"
    elif key_id != rule.key_id:
        reason = "key_id_mismatch"
    elif signature is None:
        reason = "signature_header_missing"
    else:
        signed_material = canonical_message_authenticity_bytes(
            envelope,
            algorithm=rule.algorithm,
            key_id=key_id,
            signed_fields=rule.signed_fields,
        )
        if not _verify_signature(
            algorithm=rule.algorithm,
            key=key,
            signature=signature,
            signed_material=signed_material,
        ):
            reason = "signature_invalid"

    return reason


def _normalized_header(
    headers: Mapping[str, str],
    header_name: str,
    *,
    normalize_algorithm: bool = False,
) -> str | None:
    """Read one authenticity header without exposing its value in errors."""

    value = case_insensitive_header(headers, header_name)
    if value is None:
        return None
    rendered = value.strip()
    if not rendered:
        return None
    if normalize_algorithm:
        return rendered.lower().replace("_", "-")
    return rendered


def _signature_bytes(headers: Mapping[str, str], header_name: str) -> bytes | None:
    """Decode a base64 signature header with conservative bounds."""

    value = case_insensitive_header(headers, header_name)
    if value is None:
        return None
    rendered = value.strip()
    if not rendered:
        return None
    if len(rendered) > MAX_SIGNATURE_BASE64_CHARS:
        return b""
    try:
        decoded = base64.b64decode(rendered.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        return b""
    if len(decoded) > MAX_SIGNATURE_BYTES:
        return b""
    return decoded


def _verify_signature(
    *,
    algorithm: str,
    key: bytes,
    signature: bytes,
    signed_material: bytes,
) -> bool:
    """Verify one signature using an allow-listed algorithm."""

    if algorithm == "hmac-sha256":
        expected = hmac.new(key, signed_material, hashlib.sha256).digest()
        return hmac.compare_digest(signature, expected)
    if algorithm == "ed25519":
        return _verify_ed25519_signature(key=key, signature=signature, data=signed_material)
    raise ConfigurationError("message_authenticity algorithm is not supported")


def _verify_ed25519_signature(*, key: bytes, signature: bytes, data: bytes) -> bool:
    """Verify an Ed25519 signature with a lazily imported crypto backend."""

    public_key = _ed25519_public_key_from_bytes(key)
    invalid_signature_error = _ed25519_invalid_signature_error()
    try:
        public_key.verify(signature, data)
    except invalid_signature_error:
        return False
    return True


def _validate_ed25519_public_key(key: bytes) -> None:
    """Validate an Ed25519 public key during runtime policy preparation."""

    _ed25519_public_key_from_bytes(key)


def _ed25519_public_key_from_bytes(key: bytes) -> Any:
    """Return a cryptography Ed25519 public key or raise a safe config error."""

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional extra installation.
        raise ConfigurationError(
            "Ed25519 message authenticity requires the optional crypto extra: "
            'pip install "nats-sinks[crypto]"'
        ) from exc

    try:
        return Ed25519PublicKey.from_public_bytes(key)
    except ValueError as exc:
        raise ConfigurationError("message_authenticity Ed25519 public key is invalid") from exc


def _ed25519_invalid_signature_error() -> type[Exception]:
    """Return the optional backend's invalid-signature exception type."""

    try:
        from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - guarded by public key loading.
        raise ConfigurationError(
            "Ed25519 message authenticity requires the optional crypto extra: "
            'pip install "nats-sinks[crypto]"'
        ) from exc
    return InvalidSignature


def _violation(
    index: int,
    envelope: NatsEnvelope,
    rule_subject: str,
    reason: str,
) -> MessageAuthenticityViolation:
    """Create one sanitized authenticity violation."""

    return MessageAuthenticityViolation(
        index=index,
        subject=envelope.subject,
        rule_subject=rule_subject,
        reason=reason,
    )
