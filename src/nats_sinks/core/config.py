# SPDX-License-Identifier: Apache-2.0
"""JSON configuration models and safe loading helpers.

The configuration module is the single entry point for converting user-provided
configuration files into validated runtime objects.  Runtime configuration is
intentionally JSON-only: JSON has a small parsing surface, maps directly to the
Pydantic model tree, and avoids parser features that can surprise operators
during deployment.

The loader performs three tasks in a strict order.  First, it reads a UTF-8 JSON
document and verifies that the root value is an object.  Second, it applies a
small allow-list of environment variable overrides for values commonly injected
by deployment platforms.  Third, it validates the final structure with Pydantic
models using `extra="forbid"` on the core sections so misspelled keys fail fast.

This module also owns redaction for CLI output and logs.  It never resolves sink
passwords while rendering an effective configuration and it treats fields with
names such as password, token, secret, credentials, and private_key as sensitive.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import os
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.message_metadata import (
    DEFAULT_CLASSIFICATION_HEADER,
    DEFAULT_LABELS_HEADER,
    DEFAULT_PRIORITY_HEADER,
    normalise_labels_value,
    normalise_metadata_value,
)
from nats_sinks.core.subjects import matches_subject, validate_subject_pattern

SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "private_key",
    "credentials",
    "creds",
    "key_b64",
    "key_material",
)
AES_256_KEY_BYTES = 32
MAX_ENCRYPTION_KEY_ID_LENGTH = 128


def _decode_aes_256_key(value: str, *, source: str) -> bytes:
    """Decode base64 AES key material and require a 256-bit key.

    The runtime stores crypto material outside normal configuration whenever
    possible.  Direct key values are still supported for tests and controlled
    environments, but they are validated eagerly so malformed material fails
    before the runner begins processing JetStream messages.
    """

    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ConfigurationError(f"{source} must be base64 encoded") from exc
    if len(decoded) != AES_256_KEY_BYTES:
        raise ConfigurationError(f"{source} must decode to exactly 32 bytes for AES-256")
    return decoded


class NatsConfig(BaseModel):
    """NATS and JetStream connection configuration.

    Authentication fields map directly to supported `nats-py` connection
    options.  The preferred production shape keeps secret values out of the JSON
    file by using `password_env` or `token_env`; the process resolves those
    variables only when constructing a live NATS connection.  Redacted
    configuration output therefore shows the variable names without printing
    resolved secret values.

    Bcrypted passwords and tokens are a NATS server-side storage detail.  A
    client still authenticates with the clear-text value, ideally read from a
    secret manager or environment variable and always protected in transit with
    TLS.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = "nats://localhost:4222"
    stream: str
    consumer: str
    subject: str
    durable: bool = True
    name: str | None = None
    user: str | None = None
    password: str | None = None
    password_env: str | None = None
    token: str | None = None
    token_env: str | None = None
    creds_file: str | None = None
    nkey_seed_file: str | None = None
    tls_ca_file: str | None = None
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    tls_verify: bool = True

    @model_validator(mode="after")
    def validate_secret_sources(self) -> NatsConfig:
        """Reject ambiguous secret sources and incomplete TLS key settings."""

        if self.password is not None and self.password_env is not None:
            raise ValueError("configure either nats.password or nats.password_env, not both")
        if self.token is not None and self.token_env is not None:
            raise ValueError("configure either nats.token or nats.token_env, not both")
        if self.tls_key_file is not None and self.tls_cert_file is None:
            raise ValueError("nats.tls_key_file requires nats.tls_cert_file")
        return self

    def resolve_password(self) -> str | None:
        """Resolve the NATS password only when opening a connection."""

        if self.password is not None:
            return self.password
        if self.password_env is None:
            return None
        password = os.getenv(self.password_env)
        if password is None:
            raise ConfigurationError(f"environment variable {self.password_env} is not set")
        return password

    def resolve_token(self) -> str | None:
        """Resolve the NATS token only when opening a connection."""

        if self.token is not None:
            return self.token
        if self.token_env is None:
            return None
        token = os.getenv(self.token_env)
        if token is None:
            raise ConfigurationError(f"environment variable {self.token_env} is not set")
        return token


class DeliveryConfig(BaseModel):
    """Delivery behavior. ACK policy is intentionally fixed to commit-then-ack."""

    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=100, ge=1, le=10_000)
    batch_timeout_ms: int = Field(default=1000, ge=1)
    max_in_flight_batches: int = Field(default=1, ge=1, le=64)
    ack_policy: Literal["after_sink_commit"] = "after_sink_commit"
    max_retries: int = Field(default=5, ge=0)
    retry_backoff_ms: int = Field(default=1000, ge=0)
    temporary_failure_action: Literal["nak", "leave_unacked"] = "nak"
    prefer_safe_duplication: bool = True


class DeadLetterConfig(BaseModel):
    """Dead-letter queue publication settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    subject: str | None = None
    include_payload: bool = True
    include_headers: bool = True
    include_error: bool = True


class LoggingConfig(BaseModel):
    """Logging settings."""

    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    payload_logging: bool = False


class MetricsConfig(BaseModel):
    """Metrics settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    namespace: str = "nats_sinks"


class MessageMetadataFieldConfig(BaseModel):
    """How the core resolves one application-level metadata field.

    A field can be supplied by a NATS header or by a deployment default.  If the
    header is present but empty, nats-sinks stores a null value for that message
    rather than applying the default.  This lets publishers explicitly say "no
    priority" or "no classification" when needed.
    """

    model_config = ConfigDict(extra="forbid")

    header: str
    default: str | None = None

    @field_validator("header")
    @classmethod
    def validate_header(cls, value: str) -> str:
        """Require a usable header name for runtime extraction."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("message metadata header must not be empty")
        if "\n" in rendered or "\r" in rendered:
            raise ValueError("message metadata header must not contain newlines")
        return rendered

    @field_validator("default", mode="before")
    @classmethod
    def normalize_default(cls, value: object) -> object:
        """Treat blank defaults as null so config output is predictable."""

        if value is None:
            return None
        return normalise_metadata_value(value)


class MessageMetadataLabelsConfig(BaseModel):
    """How the core resolves zero or more labels for one message.

    Labels can be supplied as semicolon-separated text in headers and JSON
    config, or as a JSON array in config.  They are normalized into an immutable
    tuple before a sink receives the envelope.
    """

    model_config = ConfigDict(extra="forbid")

    header: str
    default: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("header")
    @classmethod
    def validate_header(cls, value: str) -> str:
        """Require a usable header name for runtime extraction."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("message metadata labels header must not be empty")
        if "\n" in rendered or "\r" in rendered:
            raise ValueError("message metadata labels header must not contain newlines")
        return rendered

    @field_validator("default", mode="before")
    @classmethod
    def normalize_default(cls, value: object) -> tuple[str, ...]:
        """Accept semicolon-separated text or JSON arrays for label defaults."""

        return normalise_labels_value(value)


class MessageMetadataRuleConfig(BaseModel):
    """Subject-specific defaults for application message metadata.

    A rule does not change which headers are read.  Headers remain global so
    publishers and operators can reason about one stable metadata contract.
    Rules only provide subject-specific defaults when the relevant header is
    absent.  If a publisher sends the configured header, including an empty
    value, that header remains authoritative.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    priority: str | None = None
    classification: str | None = None
    labels: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        """Validate rule subjects with the same syntax as NATS wildcards."""

        return validate_subject_pattern(value)

    @field_validator("priority", "classification", mode="before")
    @classmethod
    def normalize_default(cls, value: object) -> object:
        """Normalize rule defaults exactly like global metadata defaults."""

        if value is None:
            return None
        return normalise_metadata_value(value)

    @field_validator("labels", mode="before")
    @classmethod
    def normalize_labels_default(cls, value: object) -> tuple[str, ...]:
        """Accept semicolon-separated text or JSON arrays for rule labels."""

        return normalise_labels_value(value)

    @model_validator(mode="after")
    def validate_rule_has_default(self) -> MessageMetadataRuleConfig:
        """Reject no-op rules that match a subject but set no defaults."""

        if (
            "priority" not in self.model_fields_set
            and "classification" not in self.model_fields_set
            and "labels" not in self.model_fields_set
        ):
            raise ValueError(
                "message metadata subject rule must set priority, classification, labels, "
                "or a combination of those fields"
            )
        return self

    def has_priority_default(self) -> bool:
        """Return whether this rule explicitly controls priority defaults."""

        return "priority" in self.model_fields_set

    def has_classification_default(self) -> bool:
        """Return whether this rule explicitly controls classification defaults."""

        return "classification" in self.model_fields_set

    def has_labels_default(self) -> bool:
        """Return whether this rule explicitly controls label defaults."""

        return "labels" in self.model_fields_set


class MessageMetadataConfig(BaseModel):
    """Application metadata fields normalized onto every `NatsEnvelope`.

    The fields are optional and sink-neutral.  Oracle persists them in dedicated
    columns, file sink persists them in every JSON record, and future sinks
    should carry the same normalized values unless their destination contract
    explicitly says otherwise.  Optional subject rules let operators apply
    different defaults to different subject families while preserving one
    global header contract.
    """

    model_config = ConfigDict(extra="forbid")

    priority: MessageMetadataFieldConfig = Field(
        default_factory=lambda: MessageMetadataFieldConfig(header=DEFAULT_PRIORITY_HEADER)
    )
    classification: MessageMetadataFieldConfig = Field(
        default_factory=lambda: MessageMetadataFieldConfig(header=DEFAULT_CLASSIFICATION_HEADER)
    )
    labels: MessageMetadataLabelsConfig = Field(
        default_factory=lambda: MessageMetadataLabelsConfig(header=DEFAULT_LABELS_HEADER)
    )
    rules: list[MessageMetadataRuleConfig] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def fill_default_headers(cls, value: object) -> object:
        """Allow env overrides to set only defaults while headers stay standard."""

        if not isinstance(value, dict):
            return value
        prepared = dict(value)
        priority = prepared.get("priority")
        if isinstance(priority, dict) and "header" not in priority:
            prepared["priority"] = {**priority, "header": DEFAULT_PRIORITY_HEADER}
        classification = prepared.get("classification")
        if isinstance(classification, dict) and "header" not in classification:
            prepared["classification"] = {
                **classification,
                "header": DEFAULT_CLASSIFICATION_HEADER,
            }
        labels = prepared.get("labels")
        if isinstance(labels, dict) and "header" not in labels:
            prepared["labels"] = {**labels, "header": DEFAULT_LABELS_HEADER}
        return prepared

    def priority_default_for_subject(self, subject: str) -> str | None:
        """Return the priority default for a subject after ordered rules."""

        for rule in self.rules:
            if rule.has_priority_default() and matches_subject(rule.subject, subject):
                return rule.priority
        return self.priority.default

    def classification_default_for_subject(self, subject: str) -> str | None:
        """Return the classification default for a subject after ordered rules."""

        for rule in self.rules:
            if rule.has_classification_default() and matches_subject(rule.subject, subject):
                return rule.classification
        return self.classification.default

    def labels_default_for_subject(self, subject: str) -> tuple[str, ...]:
        """Return label defaults for a subject after ordered rules."""

        for rule in self.rules:
            if rule.has_labels_default() and matches_subject(rule.subject, subject):
                return rule.labels
        return self.labels.default


class EncryptionRuleConfig(BaseModel):
    """Subject-specific override for framework-level payload encryption.

    Rules let operators encrypt only selected subjects, exempt selected
    subjects from a global encryption policy, or use different key identifiers
    and key material for different subject families.  They are evaluated in the
    order shown in the JSON configuration and the first matching rule wins.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    enabled: bool = True
    algorithm: Literal["aes-256-gcm", "aes-256-ccm"] | None = None
    key_id: str | None = None
    key_b64: str | None = None
    key_b64_env: str | None = None
    nonce_size_bytes: int | None = Field(default=None, ge=7, le=13)
    tag_length: int | None = None

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        """Validate rule subjects with the same syntax as NATS wildcards."""

        return validate_subject_pattern(value)

    @field_validator("algorithm", mode="before")
    @classmethod
    def normalize_algorithm(cls, value: object) -> object:
        """Accept common uppercase spellings while storing canonical values."""

        if isinstance(value, str):
            return value.strip().lower().replace("_", "-")
        return value

    @field_validator("key_id")
    @classmethod
    def validate_key_id(cls, value: str | None) -> str | None:
        """Require usable non-secret identifiers when a rule overrides key IDs."""

        if value is None:
            return None
        if not value.strip():
            raise ValueError("encryption rule key_id must not be empty")
        if len(value) > MAX_ENCRYPTION_KEY_ID_LENGTH:
            raise ValueError(
                f"encryption rule key_id must not exceed {MAX_ENCRYPTION_KEY_ID_LENGTH} characters"
            )
        return value

    @field_validator("tag_length")
    @classmethod
    def validate_tag_length(cls, value: int | None) -> int | None:
        """Validate optional AES-CCM tag lengths accepted by `cryptography`."""

        if value is None:
            return None
        if value not in {4, 6, 8, 10, 12, 14, 16}:
            raise ValueError("encryption rule tag_length must be one of 4, 6, 8, 10, 12, 14, 16")
        return value

    @model_validator(mode="after")
    def validate_key_sources(self) -> EncryptionRuleConfig:
        """Reject ambiguous key source settings on a single rule."""

        if self.key_b64 is not None and self.key_b64_env is not None:
            raise ValueError("configure either encryption rule key_b64 or key_b64_env")
        if self.key_b64 is not None:
            _decode_aes_256_key(self.key_b64, source=f"encryption rule {self.subject}.key_b64")
        if self.key_b64_env is not None and not self.key_b64_env.strip():
            raise ValueError("encryption rule key_b64_env must not be empty")
        return self


class EncryptionConfig(BaseModel):
    """Optional framework-level payload encryption configuration.

    Encryption is a core concern because it must happen before any destination
    sink receives message data.  Only `NatsEnvelope.data` is encrypted.  The
    subject, headers, stream name, sequence numbers, and timing metadata remain
    available to sinks for routing, idempotency, and operations.

    Key material may be provided directly through `key_b64` or, preferably, by
    naming an environment variable with `key_b64_env`.  The expected value is a
    base64-encoded 32-byte key.  Direct key values are redacted by
    `show-effective-config` and should not be committed to source control.

    The optional `rules` list adds per-subject policy.  Rules are evaluated in
    configuration order, first match wins, and unmatched subjects fall back to
    the top-level `enabled` setting.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    algorithm: Literal["aes-256-gcm", "aes-256-ccm"] = "aes-256-gcm"
    key_id: str = "default"
    key_b64: str | None = None
    key_b64_env: str | None = None
    nonce_size_bytes: int = Field(default=12, ge=7, le=13)
    tag_length: int = Field(default=16)
    rules: list[EncryptionRuleConfig] = Field(default_factory=list)

    @field_validator("algorithm", mode="before")
    @classmethod
    def normalize_algorithm(cls, value: object) -> object:
        """Accept common uppercase spellings while storing canonical values."""

        if isinstance(value, str):
            return value.strip().lower().replace("_", "-")
        return value

    @field_validator("key_id")
    @classmethod
    def validate_key_id(cls, value: str) -> str:
        """Require a stable non-secret key identifier for encrypted payloads."""

        if not value.strip():
            raise ValueError("encryption.key_id must not be empty")
        if len(value) > MAX_ENCRYPTION_KEY_ID_LENGTH:
            raise ValueError(
                f"encryption.key_id must not exceed {MAX_ENCRYPTION_KEY_ID_LENGTH} characters"
            )
        return value

    @field_validator("tag_length")
    @classmethod
    def validate_tag_length(cls, value: int) -> int:
        """Validate AES-CCM tag lengths accepted by `cryptography`."""

        if value not in {4, 6, 8, 10, 12, 14, 16}:
            raise ValueError("encryption.tag_length must be one of 4, 6, 8, 10, 12, 14, 16")
        return value

    @model_validator(mode="after")
    def validate_key_sources(self) -> EncryptionConfig:
        """Reject ambiguous or missing key sources for enabled encryption.

        Top-level key material is required when the global policy encrypts
        unmatched subjects.  Subject rules may either inherit that material or
        provide their own key source.  Disabled rules need no key because they
        explicitly pass matching subjects through unchanged.
        """

        if self.key_b64 is not None and self.key_b64_env is not None:
            raise ValueError("configure either encryption.key_b64 or encryption.key_b64_env")
        if self.enabled and self.key_b64 is None and self.key_b64_env is None:
            raise ValueError("encryption.key_b64_env or encryption.key_b64 is required")
        if self.key_b64 is not None:
            _decode_aes_256_key(self.key_b64, source="encryption.key_b64")
        if self.key_b64_env is not None and not self.key_b64_env.strip():
            raise ValueError("encryption.key_b64_env must not be empty")
        for index, rule in enumerate(self.rules):
            if not rule.enabled:
                continue
            if self._rule_needs_key(rule) and self.key_b64 is None and self.key_b64_env is None:
                raise ValueError(
                    "encryption.rules["
                    f"{index}"
                    "].key_b64_env or key_b64 is required because no top-level "
                    "encryption key is configured"
                )
        return self

    def resolve_key(self) -> bytes:
        """Resolve and validate the AES-256 key at the runtime boundary."""

        if self.key_b64 is not None:
            return _decode_aes_256_key(self.key_b64, source="encryption.key_b64")
        if self.key_b64_env is None:
            raise ConfigurationError("encryption key material is not configured")
        value = os.getenv(self.key_b64_env)
        if value is None:
            raise ConfigurationError(f"environment variable {self.key_b64_env} is not set")
        return _decode_aes_256_key(value, source=f"environment variable {self.key_b64_env}")

    def effective_rule_config(self, rule: EncryptionRuleConfig) -> EncryptionConfig:
        """Build the concrete encryption configuration used by one rule.

        Rules inherit any omitted algorithm, key identifier, key source, nonce
        size, and tag length from the top-level encryption section.  Returning a
        normal `EncryptionConfig` lets the runtime reuse the same tested
        encrypt/decrypt code path for global and subject-specific encryption.
        """

        return EncryptionConfig(
            enabled=rule.enabled,
            algorithm=rule.algorithm or self.algorithm,
            key_id=rule.key_id or self.key_id,
            key_b64=rule.key_b64 if rule.key_b64 is not None else self.key_b64,
            key_b64_env=rule.key_b64_env if rule.key_b64_env is not None else self.key_b64_env,
            nonce_size_bytes=rule.nonce_size_bytes or self.nonce_size_bytes,
            tag_length=rule.tag_length or self.tag_length,
        )

    @staticmethod
    def _rule_needs_key(rule: EncryptionRuleConfig) -> bool:
        """Return whether an enabled rule needs inherited key material."""

        return rule.key_b64 is None and rule.key_b64_env is None


class SinkConfig(BaseModel):
    """Raw sink configuration selected through the safe registry."""

    model_config = ConfigDict(extra="allow")

    type: str


class AppConfig(BaseModel):
    """Top-level application configuration."""

    model_config = ConfigDict(extra="forbid")

    nats: NatsConfig
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    dead_letter: DeadLetterConfig = Field(default_factory=DeadLetterConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    message_metadata: MessageMetadataConfig = Field(default_factory=MessageMetadataConfig)
    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)
    sink: SinkConfig


ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "NATS_SINKS_NATS_URL": ("nats", "url"),
    "NATS_SINKS_NATS_STREAM": ("nats", "stream"),
    "NATS_SINKS_NATS_CONSUMER": ("nats", "consumer"),
    "NATS_SINKS_NATS_SUBJECT": ("nats", "subject"),
    "NATS_SINKS_LOG_LEVEL": ("logging", "level"),
    "NATS_SINKS_ENCRYPTION_ENABLED": ("encryption", "enabled"),
    "NATS_SINKS_ENCRYPTION_ALGORITHM": ("encryption", "algorithm"),
    "NATS_SINKS_ENCRYPTION_KEY_ID": ("encryption", "key_id"),
    "NATS_SINKS_ENCRYPTION_KEY_B64_ENV": ("encryption", "key_b64_env"),
    "NATS_SINKS_PRIORITY_HEADER": ("message_metadata", "priority", "header"),
    "NATS_SINKS_PRIORITY_DEFAULT": ("message_metadata", "priority", "default"),
    "NATS_SINKS_CLASSIFICATION_HEADER": (
        "message_metadata",
        "classification",
        "header",
    ),
    "NATS_SINKS_CLASSIFICATION_DEFAULT": (
        "message_metadata",
        "classification",
        "default",
    ),
    "NATS_SINKS_LABELS_HEADER": ("message_metadata", "labels", "header"),
    "NATS_SINKS_LABELS_DEFAULT": ("message_metadata", "labels", "default"),
    "NATS_SINKS_SINK_TYPE": ("sink", "type"),
}


def _set_nested(config: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    current = config
    for part in path[:-1]:
        next_value = current.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ConfigurationError(f"cannot apply environment override for {'.'.join(path)}")
        current = next_value
    current[path[-1]] = value


def apply_environment_overrides(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Apply a small, explicit set of environment overrides."""

    config = copy.deepcopy(raw_config)
    for env_name, path in ENV_OVERRIDES.items():
        value = os.getenv(env_name)
        if value is not None:
            _set_nested(config, path, value)
    return config


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON configuration file and require an object at the root."""

    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"failed to read configuration file {file_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"configuration file {file_path} is not valid JSON") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration root must be a mapping")
    return raw


def load_config(path: str | Path, *, env_overrides: bool = True) -> AppConfig:
    """Load and validate an application configuration."""

    raw = load_json(path)
    if env_overrides:
        raw = apply_environment_overrides(raw)
    try:
        return AppConfig.model_validate(raw)
    except PydanticValidationError as exc:
        raise ConfigurationError(str(exc)) from exc


def _redact_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    key_lower = key.lower()
    if any(part in key_lower for part in SENSITIVE_KEY_PARTS):
        return "********"
    if key_lower in {"dsn", "url"} and isinstance(value, str) and "@" in value:
        return "********"
    return value


def redact_mapping(value: Any) -> Any:
    """Return a copy of a mapping/list tree with secret-looking values redacted."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: redact_mapping(_redact_value(str(key), item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value


def redacted_config(config: AppConfig) -> dict[str, Any]:
    """Return a redacted, serializable effective configuration."""

    return cast("dict[str, Any]", redact_mapping(config.model_dump(mode="json")))
