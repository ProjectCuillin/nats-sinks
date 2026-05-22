# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
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
import re
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.errors import ValidationError as FrameworkValidationError
from nats_sinks.core.message_metadata import (
    DEFAULT_CLASSIFICATION_HEADER,
    DEFAULT_LABELS_HEADER,
    DEFAULT_PRIORITY_HEADER,
    normalise_labels_value,
    normalise_metadata_value,
)
from nats_sinks.core.mission_metadata import (
    DEFAULT_MAX_MISSION_METADATA_BYTES,
    DEFAULT_MISSION_METADATA_HEADER,
    MAX_MISSION_METADATA_BYTES,
    normalize_mission_metadata_object,
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
MAX_CONFIG_BYTES = 1_048_576
NATS_ALLOWED_URL_SCHEMES = frozenset({"nats", "tls", "ws", "wss"})
PRIORITY_LANE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PRE_SINK_POLICY_MISSION_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
ASCII_CONTROL_MAX = 31
ASCII_DELETE = 127
MAX_POLICY_LABEL_LENGTH = 128
MAX_POLICY_PAYLOAD_BYTES = 1_073_741_824
POLICY_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "private_key",
    "credential",
    "api_key",
    "key_material",
)


def _validate_nats_server_url(value: str, *, field: str) -> str:
    """Validate a NATS client URL with an explicit transport allow list."""

    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field} must not be empty")
    if "\x00" in rendered or "\n" in rendered or "\r" in rendered:
        raise ValueError(f"{field} must not contain control characters")
    parsed = urlsplit(rendered)
    if parsed.scheme not in NATS_ALLOWED_URL_SCHEMES:
        allowed = ", ".join(sorted(NATS_ALLOWED_URL_SCHEMES))
        raise ValueError(f"{field} must use one of these schemes: {allowed}")
    if not parsed.netloc:
        raise ValueError(f"{field} must include a host")
    return rendered


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
    urls: list[str] = Field(default_factory=list)
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
    allow_reconnect: bool = True
    connect_timeout_seconds: int = Field(default=2, ge=1, le=300)
    reconnect_time_wait_seconds: int = Field(default=2, ge=0, le=3600)
    max_reconnect_attempts: int = Field(default=60, ge=-1, le=1_000_000)
    ping_interval_seconds: int = Field(default=120, ge=1, le=3600)
    max_outstanding_pings: int = Field(default=2, ge=1, le=100)
    pending_size_bytes: int = Field(default=2_097_152, ge=1024, le=1_073_741_824)
    drain_timeout_seconds: int = Field(default=30, ge=1, le=3600)

    @model_validator(mode="after")
    def validate_secret_sources(self) -> NatsConfig:
        """Reject ambiguous secret sources and incomplete TLS key settings."""

        if self.password is not None and self.password_env is not None:
            raise ValueError("configure either nats.password or nats.password_env, not both")
        if self.token is not None and self.token_env is not None:
            raise ValueError("configure either nats.token or nats.token_env, not both")
        has_password_source = self.password is not None or self.password_env is not None
        has_user_password_auth = self.user is not None or has_password_source
        if has_password_source and self.user is None:
            raise ValueError("nats.user is required when nats.password or nats.password_env is set")
        if self.user is not None and not has_password_source:
            raise ValueError("nats.password or nats.password_env is required when nats.user is set")
        auth_modes = [
            has_user_password_auth,
            self.token is not None or self.token_env is not None,
            self.creds_file is not None,
            self.nkey_seed_file is not None,
        ]
        if sum(1 for enabled in auth_modes if enabled) > 1:
            raise ValueError("configure a single NATS authentication method")
        if self.tls_key_file is not None and self.tls_cert_file is None:
            raise ValueError("nats.tls_key_file requires nats.tls_cert_file")
        return self

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Reject empty or control-character NATS URLs before connection setup."""

        return _validate_nats_server_url(value, field="nats.url")

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, value: list[str]) -> list[str]:
        """Validate optional NATS seed URLs while preserving configured order."""

        rendered_urls: list[str] = []
        for item in value:
            rendered_urls.append(_validate_nats_server_url(item, field="nats.urls"))
        return rendered_urls

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


class PriorityLaneConfig(BaseModel):
    """One weighted processing lane used for in-batch priority scheduling.

    A lane maps one or more normalized priority values, such as `urgent` or
    `routine`, to a lane name and a small positive weight.  The runner uses
    these weights only after messages have already been fetched into a bounded
    batch.  They do not change JetStream server-side delivery order and they do
    not weaken commit-then-acknowledge processing.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    priorities: tuple[str, ...] = Field(default_factory=tuple)
    weight: int = Field(default=1, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Validate lane names before they can appear in policy or metrics."""

        rendered = value.strip().lower()
        if not PRIORITY_LANE_NAME_RE.fullmatch(rendered):
            raise ValueError(
                "priority lane names must start with a lowercase letter and may contain "
                "only lowercase letters, digits, underscores, or hyphens"
            )
        return rendered

    @field_validator("priorities", mode="before")
    @classmethod
    def normalize_priorities(cls, value: object) -> tuple[str, ...]:
        """Normalize configured priority values into a case-insensitive tuple."""

        if value is None:
            return ()
        raw_values: list[object]
        if isinstance(value, str):
            raw_values = [value]
        elif isinstance(value, (list, tuple, set, frozenset)):
            raw_values = list(value)
        else:
            raise ValueError("priority lane priorities must be a string or list of strings")

        priorities: list[str] = []
        seen: set[str] = set()
        for item in raw_values:
            rendered = normalise_metadata_value(item)
            if rendered is None:
                raise ValueError("priority lane priorities must not contain empty values")
            normalized = rendered.casefold()
            if any(
                ord(character) <= ASCII_CONTROL_MAX or ord(character) == ASCII_DELETE
                for character in normalized
            ):
                raise ValueError("priority lane priorities must not contain control characters")
            if normalized in seen:
                continue
            priorities.append(normalized)
            seen.add(normalized)
        return tuple(priorities)


class PriorityLanesConfig(BaseModel):
    """Optional priority-aware processing policy for already-fetched batches.

    Priority lanes are disabled by default so existing deployments keep exact
    arrival-order sink delivery.  When enabled, the runner maps each envelope's
    normalized `priority` metadata to a configured lane, orders the current
    batch with weighted round-robin, then calls the sink once with that ordered
    batch.  ACK behavior remains unchanged: ACK is still sent only after sink
    durable success.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    default_lane: str = "default"
    unknown_priority_action: Literal["default_lane", "reject"] = "default_lane"
    max_priority_value_length: int = Field(default=64, ge=1, le=256)
    lanes: list[PriorityLaneConfig] = Field(
        default_factory=lambda: [PriorityLaneConfig(name="default", priorities=(), weight=1)]
    )

    @field_validator("default_lane")
    @classmethod
    def validate_default_lane(cls, value: str) -> str:
        """Validate the lane name used for missing or unknown priority values."""

        rendered = value.strip().lower()
        if not PRIORITY_LANE_NAME_RE.fullmatch(rendered):
            raise ValueError("delivery.priority_lanes.default_lane must be a valid lane name")
        return rendered

    @model_validator(mode="after")
    def validate_lane_policy(self) -> PriorityLanesConfig:
        """Reject ambiguous lane policies before any messages are fetched."""

        lane_names: set[str] = set()
        priority_to_lane: dict[str, str] = {}
        for lane in self.lanes:
            if lane.name in lane_names:
                raise ValueError(f"duplicate priority lane name: {lane.name}")
            lane_names.add(lane.name)
            for priority in lane.priorities:
                if len(priority) > self.max_priority_value_length:
                    raise ValueError(
                        "priority lane priority values must not exceed "
                        f"{self.max_priority_value_length} characters"
                    )
                existing_lane = priority_to_lane.get(priority)
                if existing_lane is not None:
                    raise ValueError(
                        f"priority value {priority!r} is assigned to both "
                        f"{existing_lane!r} and {lane.name!r}"
                    )
                priority_to_lane[priority] = lane.name

        if self.default_lane not in lane_names:
            raise ValueError(
                f"delivery.priority_lanes.default_lane {self.default_lane!r} "
                "must match one configured lane"
            )
        return self


class DeliveryConfig(BaseModel):
    """Delivery behavior. ACK policy is intentionally fixed to commit-then-ack.

    Retry settings control only active delayed NAK behavior after retryable
    failures.  They never permit early ACK.  When the active retry budget is
    exhausted, the runner leaves messages redeliverable so JetStream
    `AckWait`, `MaxDeliver`, and advisory policy remain the final authority.
    """

    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=100, ge=1, le=10_000)
    batch_timeout_ms: int = Field(default=1000, ge=1)
    max_in_flight_batches: int = Field(default=1, ge=1, le=64)
    ack_policy: Literal["after_sink_commit"] = "after_sink_commit"
    max_retries: int = Field(default=5, ge=0, le=1_000_000)
    retry_backoff_ms: int = Field(default=1000, ge=0, le=3_600_000)
    retry_backoff_max_ms: int = Field(default=60_000, ge=0, le=3_600_000)
    retry_backoff_mode: Literal["fixed", "linear", "exponential"] = "exponential"
    retry_backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    retry_jitter: Literal["none", "full", "equal"] = "full"
    temporary_failure_action: Literal["nak", "leave_unacked"] = "nak"
    prefer_safe_duplication: bool = True
    priority_lanes: PriorityLanesConfig = Field(default_factory=PriorityLanesConfig)

    @model_validator(mode="after")
    def validate_retry_backoff_cap(self) -> DeliveryConfig:
        """Reject retry policies whose cap is lower than the base delay.

        Allowing a cap below the base delay is technically possible, but it is
        easy for operators to misread in production.  Requiring the cap to be
        equal to or above the base delay keeps the policy reviewable.
        """

        if self.retry_backoff_max_ms < self.retry_backoff_ms:
            raise ValueError(
                "delivery.retry_backoff_max_ms must be greater than or equal to "
                "delivery.retry_backoff_ms"
            )
        return self


class DeadLetterConfig(BaseModel):
    """Dead-letter queue publication settings.

    `ack_term_after_publish` is deliberately disabled by default.  When it is
    enabled, the runner sends JetStream `AckTerm` only after the DLQ publish has
    succeeded.  It is never a sink-success acknowledgement and must not be used
    as a shortcut around commit-then-acknowledge processing.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    subject: str | None = None
    include_payload: bool = True
    include_headers: bool = True
    include_error: bool = True
    ack_term_after_publish: bool = False


class PreSinkPolicyRuleConfig(BaseModel):
    """One allow-listed pre-sink policy rule.

    Rules are data-only configuration, not executable expressions. Every
    enabled rule is evaluated against matching subjects before any sink sees
    the message. If a rule fails, the message follows the permanent-failure
    path and can be published to DLQ before ACK.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str = ">"
    require_priority: bool = False
    require_classification: bool = False
    required_labels: tuple[str, ...] = Field(default_factory=tuple)
    require_mission_metadata: bool = False
    require_encrypted_payload: bool = False
    max_payload_bytes: int | None = Field(default=None, ge=0, le=MAX_POLICY_PAYLOAD_BYTES)
    allowed_mission_metadata_keys: tuple[str, ...] | None = None

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        """Validate policy subject patterns with the same NATS syntax as routing."""

        return validate_subject_pattern(value)

    @field_validator("required_labels", mode="before")
    @classmethod
    def normalize_required_labels(cls, value: object) -> tuple[str, ...]:
        """Normalize and bound required labels before runtime policy checks."""

        labels = normalise_labels_value(value)
        for label in labels:
            if len(label) > MAX_POLICY_LABEL_LENGTH:
                raise ValueError(
                    f"policy required labels must not exceed {MAX_POLICY_LABEL_LENGTH} characters"
                )
            if any(
                ord(character) <= ASCII_CONTROL_MAX or ord(character) == ASCII_DELETE
                for character in label
            ):
                raise ValueError("policy required labels must not contain control characters")
        return labels

    @field_validator("allowed_mission_metadata_keys", mode="before")
    @classmethod
    def normalize_allowed_mission_metadata_keys(
        cls,
        value: object,
    ) -> tuple[str, ...] | None:
        """Validate root mission-metadata keys used by the policy allow list."""

        if value is None:
            return None
        if isinstance(value, str):
            raw_values = [value]
        elif isinstance(value, (list, tuple, set, frozenset)):
            raw_values = list(value)
        else:
            raise ValueError("policy allowed_mission_metadata_keys must be a string or list")

        keys: list[str] = []
        seen: set[str] = set()
        for item in raw_values:
            if not isinstance(item, str):
                raise ValueError("policy allowed_mission_metadata_keys must contain strings")
            key = item.strip()
            if not key:
                raise ValueError("policy allowed_mission_metadata_keys must not contain blanks")
            if "\n" in key or "\r" in key or "\x00" in key:
                raise ValueError(
                    "policy allowed_mission_metadata_keys must not contain control characters"
                )
            if PRE_SINK_POLICY_MISSION_KEY_RE.fullmatch(key) is None:
                raise ValueError(
                    "policy allowed_mission_metadata_keys must start with a letter and contain "
                    "only letters, numbers, underscores, dots, colons, or hyphens"
                )
            key_lower = key.lower()
            if any(part in key_lower for part in POLICY_SECRET_KEY_PARTS):
                raise ValueError(
                    "policy allowed_mission_metadata_keys must not contain secret-like names"
                )
            if key in seen:
                continue
            keys.append(key)
            seen.add(key)
        return tuple(keys)

    @model_validator(mode="after")
    def validate_rule_has_check(self) -> PreSinkPolicyRuleConfig:
        """Reject no-op policy rules that would make reviews misleading."""

        has_allowed_keys_check = "allowed_mission_metadata_keys" in self.model_fields_set
        if not (
            self.require_priority
            or self.require_classification
            or self.required_labels
            or self.require_mission_metadata
            or self.require_encrypted_payload
            or self.max_payload_bytes is not None
            or has_allowed_keys_check
        ):
            raise ValueError("pre-sink policy rule must configure at least one check")
        return self


class PreSinkPolicyConfig(BaseModel):
    """Destination-neutral policy gate evaluated before sink writes.

    The gate is disabled by default. Once enabled, configuration intentionally
    fails closed: at least one explicit rule is required, and unmatched subjects
    are rejected unless the operator opts into `unmatched_subject_action=allow`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    unmatched_subject_action: Literal["allow", "reject"] = "reject"
    rules: list[PreSinkPolicyRuleConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_enabled_policy_has_rules(self) -> PreSinkPolicyConfig:
        """Require explicit rules before enabling the pre-sink gate."""

        if self.enabled and not self.rules:
            raise ValueError("pre_sink_policy.enabled requires at least one rule")
        return self


class LoggingConfig(BaseModel):
    """Logging settings."""

    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    payload_logging: bool = False

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        """Fail closed when an unknown logging policy is configured."""

        from nats_sinks.core.logging import normalize_log_level  # noqa: PLC0415

        return normalize_log_level(value)


class MetricsConfig(BaseModel):
    """Metrics settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    namespace: str = "nats_sinks"
    snapshot_file: str | None = None

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        """Require a namespace that exporters can safely use as a metric prefix."""

        from nats_sinks.core.metrics import validate_metric_namespace  # noqa: PLC0415

        return validate_metric_namespace(value)

    @field_validator("snapshot_file")
    @classmethod
    def validate_snapshot_file(cls, value: str | None) -> str | None:
        """Reject ambiguous metrics snapshot paths before the runner starts."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("metrics.snapshot_file must not be empty")
        if "\x00" in rendered or "\n" in rendered or "\r" in rendered:
            raise ValueError("metrics.snapshot_file must not contain control characters")
        return rendered


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


class MissionMetadataRuleConfig(BaseModel):
    """Subject-specific default mission metadata.

    The rule does not inspect or transform payloads.  It only supplies a
    validated JSON metadata object when the message does not already contain
    the configured mission metadata header.  Setting `metadata` to `null`
    explicitly clears the global default for matching subjects.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    metadata: dict[str, Any] | None = None

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        """Validate rule subjects with the same syntax as NATS wildcards."""

        return validate_subject_pattern(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> object:
        """Validate configured default metadata before runtime processing."""

        if value is None:
            return None
        try:
            return normalize_mission_metadata_object(
                value,
                max_bytes=MAX_MISSION_METADATA_BYTES,
                source="mission metadata rule",
            )
        except FrameworkValidationError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def validate_rule_has_metadata_field(self) -> MissionMetadataRuleConfig:
        """Reject no-op rules that match a subject without setting metadata."""

        if "metadata" not in self.model_fields_set:
            raise ValueError("mission metadata rule must set metadata, including null if desired")
        return self


class MissionMetadataConfig(BaseModel):
    """Optional generic mission event metadata configuration.

    Mission metadata is a validated JSON object carried next to the payload.
    It is disabled by default because not every deployment needs this richer
    context.  When enabled, a publisher can provide the object through the
    configured NATS header, while operators can set safe global and
    subject-aware defaults in configuration.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    header: str = DEFAULT_MISSION_METADATA_HEADER
    default: dict[str, Any] | None = None
    rules: list[MissionMetadataRuleConfig] = Field(default_factory=list)
    max_bytes: int = Field(
        default=DEFAULT_MAX_MISSION_METADATA_BYTES,
        ge=1,
        le=MAX_MISSION_METADATA_BYTES,
    )
    allowed_profiles: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("header")
    @classmethod
    def validate_header(cls, value: str) -> str:
        """Require a safe header name for mission metadata extraction."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("mission_metadata.header must not be empty")
        if "\n" in rendered or "\r" in rendered or "\x00" in rendered:
            raise ValueError("mission_metadata.header must not contain control characters")
        return rendered

    @field_validator("default", mode="before")
    @classmethod
    def validate_default(cls, value: object) -> object:
        """Validate optional global default metadata."""

        if value is None:
            return None
        try:
            return normalize_mission_metadata_object(
                value,
                max_bytes=MAX_MISSION_METADATA_BYTES,
                source="mission metadata default",
            )
        except FrameworkValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("allowed_profiles", mode="before")
    @classmethod
    def validate_allowed_profiles(cls, value: object) -> tuple[str, ...]:
        """Normalize configured profile allow-list values."""

        if value is None:
            return ()
        if isinstance(value, str):
            raw_profiles = [value]
        elif isinstance(value, (list, tuple, set, frozenset)):
            raw_profiles = list(value)
        else:
            raise ValueError("mission_metadata.allowed_profiles must be a list of strings")

        profiles: list[str] = []
        seen: set[str] = set()
        for item in raw_profiles:
            if not isinstance(item, str):
                raise ValueError("mission_metadata.allowed_profiles must contain only strings")
            rendered = item.strip()
            if not rendered:
                raise ValueError("mission_metadata.allowed_profiles must not contain empty values")
            if "\n" in rendered or "\r" in rendered or "\x00" in rendered:
                raise ValueError(
                    "mission_metadata.allowed_profiles must not contain control characters"
                )
            if rendered in seen:
                continue
            profiles.append(rendered)
            seen.add(rendered)
        return tuple(profiles)

    @model_validator(mode="after")
    def validate_default_against_effective_limits(self) -> MissionMetadataConfig:
        """Recheck configured defaults with the operator's byte limit and profile allow-list."""

        if self.default is not None:
            try:
                self.default = normalize_mission_metadata_object(
                    self.default,
                    max_bytes=self.max_bytes,
                    allowed_profiles=self.allowed_profiles,
                    source="mission metadata default",
                )
            except FrameworkValidationError as exc:
                raise ValueError(str(exc)) from exc
        for rule in self.rules:
            if rule.metadata is not None:
                try:
                    rule.metadata = normalize_mission_metadata_object(
                        rule.metadata,
                        max_bytes=self.max_bytes,
                        allowed_profiles=self.allowed_profiles,
                        source=f"mission metadata rule {rule.subject}",
                    )
                except FrameworkValidationError as exc:
                    raise ValueError(str(exc)) from exc
        return self


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
    mission_metadata: MissionMetadataConfig = Field(default_factory=MissionMetadataConfig)
    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)
    pre_sink_policy: PreSinkPolicyConfig = Field(default_factory=PreSinkPolicyConfig)
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
    "NATS_SINKS_MISSION_METADATA_ENABLED": ("mission_metadata", "enabled"),
    "NATS_SINKS_MISSION_METADATA_HEADER": ("mission_metadata", "header"),
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


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous JSON objects instead of accepting the last duplicate key."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject Python JSON extensions such as NaN and Infinity in config files."""

    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a bounded JSON configuration file and require an object at the root."""

    file_path = Path(path)
    try:
        raw_bytes = file_path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"failed to read configuration file {file_path}") from exc
    if len(raw_bytes) > MAX_CONFIG_BYTES:
        raise ConfigurationError(
            f"configuration file {file_path} exceeds the {MAX_CONFIG_BYTES} byte limit"
        )
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"configuration file {file_path} is not valid UTF-8") from exc
    try:
        raw = json.loads(
            raw_text,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"configuration file {file_path} is not valid JSON") from exc
    except ValueError as exc:
        raise ConfigurationError(f"configuration file {file_path} is ambiguous: {exc}") from exc

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
