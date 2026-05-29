# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration for the first-party S3-compatible object sink.

S3-compatible object storage is an outbound trust boundary.  The sink therefore
accepts only operator-controlled bucket, endpoint, prefix, credential-reference,
key, duplicate-policy, metadata, compression, and timeout settings.  Message
content never selects object destinations or SDK credential sources.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.payload import PayloadStorageMode
from nats_sinks.core.retry import RetryBackoffMode, RetryJitterMode

S3KeyStrategy = Literal["idempotency_key", "stream_sequence", "message_id", "payload_sha256"]
S3DuplicatePolicy = Literal["skip_existing", "replace", "fail_existing"]
S3ObjectFormat = Literal["envelope", "payload"]
S3MetadataMode = Literal["none", "object_metadata", "sidecar"]
S3CompressionMode = Literal["none", "gzip"]
S3CredentialMode = Literal["default_chain", "environment", "profile"]
S3ServerSideEncryption = Literal["none", "AES256"]
S3DurabilityMode = Literal["operator_confirmed"]

_ASCII_CONTROL_CUTOFF = 32
_ASCII_DELETE = 127
_MAX_TCP_PORT = 65_535
_MIN_BUCKET_LENGTH = 3
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.=-]{0,127}$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_REGION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PROFILE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_METADATA_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_OBJECT_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ALLOWED_ENDPOINT_SCHEMES = frozenset({"http", "https"})
_MAX_METADATA_ITEMS = 32


def _contains_control_characters(value: str) -> bool:
    """Return true when a string contains unsafe ASCII controls."""

    return any(
        ord(character) < _ASCII_CONTROL_CUTOFF or ord(character) == _ASCII_DELETE
        for character in value
    )


def _validate_plain_text(value: str, *, field: str, maximum: int) -> str:
    """Validate bounded non-secret text."""

    rendered = value.strip()
    if rendered != value:
        raise ValueError(f"{field} must not contain surrounding whitespace")
    if not rendered:
        raise ValueError(f"{field} must not be empty")
    if len(rendered) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    if _contains_control_characters(rendered):
        raise ValueError(f"{field} must not contain control characters")
    return rendered


def _validate_optional_text(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _validate_plain_text(value, field=field, maximum=maximum)


def _validate_optional_env(value: str | None, *, field: str) -> str | None:
    """Validate an optional environment variable name used for secret material."""

    if value is None:
        return None
    rendered = _validate_plain_text(value, field=field, maximum=128)
    if not _ENV_NAME_RE.fullmatch(rendered):
        raise ValueError(
            f"{field} must be an uppercase environment variable name using letters, "
            "numbers, and underscores"
        )
    return rendered


def _is_loopback_host(host: str) -> bool:
    """Return whether a URL host is a loopback-only local testing host."""

    if host.casefold() in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _validate_prefix(value: str | None) -> str | None:
    """Validate an optional object-key prefix without repairing it."""

    if value is None:
        return None
    rendered = _validate_plain_text(value, field="sink.prefix", maximum=1024)
    if rendered.startswith("/") or rendered.endswith("/"):
        raise ValueError("sink.prefix must not start or end with '/'")
    if "//" in rendered:
        raise ValueError("sink.prefix must not contain empty path segments")
    for segment in rendered.split("/"):
        if segment in {".", ".."} or not _SAFE_SEGMENT_RE.fullmatch(segment):
            raise ValueError(
                "sink.prefix segments must start with a letter or digit and contain only "
                "letters, digits, '_', '.', '=', or '-'"
            )
    return rendered


class S3SinkConfig(BaseModel):
    """Validated configuration for ``S3Sink``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["s3"] = "s3"
    bucket: str
    prefix: str | None = None
    endpoint_url: str | None = None
    allow_http_for_local_testing: bool = False
    region_name: str | None = None
    credential_mode: S3CredentialMode = "default_chain"
    profile_name: str | None = None
    aws_access_key_id_env: str | None = None
    aws_secret_access_key_env: str | None = None
    aws_session_token_env: str | None = None
    key_strategy: S3KeyStrategy = "idempotency_key"
    key_prefix: str | None = None
    object_suffix: str = ".json"
    duplicate_policy: S3DuplicatePolicy = "skip_existing"
    object_format: S3ObjectFormat = "envelope"
    metadata_mode: S3MetadataMode = "object_metadata"
    sidecar_suffix: str = ".metadata.json"
    payload_mode: PayloadStorageMode = "json_or_envelope"
    compression: S3CompressionMode = "none"
    content_type: str = "application/json"
    object_metadata: dict[str, str] = Field(default_factory=dict)
    server_side_encryption: S3ServerSideEncryption = "none"
    max_key_bytes: int = Field(default=1024, ge=64, le=1024)
    max_object_bytes: int = Field(default=16_777_216, ge=1, le=1_073_741_824)
    max_metadata_bytes: int = Field(default=4096, ge=128, le=65_536)
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_ms: int = Field(default=250, ge=0, le=60_000)
    retry_max_backoff_ms: int = Field(default=5_000, ge=0, le=300_000)
    retry_backoff_mode: RetryBackoffMode = "exponential"
    retry_backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    retry_jitter: RetryJitterMode = "full"
    durability: S3DurabilityMode = "operator_confirmed"

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, value: str) -> str:
        """Validate a conservative DNS-style S3 bucket name."""

        rendered = _validate_plain_text(value, field="sink.bucket", maximum=63)
        if len(rendered) < _MIN_BUCKET_LENGTH or not _BUCKET_RE.fullmatch(rendered):
            raise ValueError(
                "sink.bucket must be 3-63 lowercase DNS characters using letters, "
                "numbers, dots, and hyphens"
            )
        if ".." in rendered or ".-" in rendered or "-." in rendered:
            raise ValueError("sink.bucket must not contain adjacent dot or hyphen separators")
        try:
            ipaddress.ip_address(rendered)
        except ValueError:
            return rendered
        raise ValueError("sink.bucket must not be formatted as an IP address")

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, value: str | None) -> str | None:
        """Validate optional object key prefixes."""

        return _validate_prefix(value)

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, value: str | None) -> str | None:
        """Validate optional S3-compatible endpoint URLs without credentials."""

        if value is None:
            return None
        rendered = _validate_plain_text(value, field="sink.endpoint_url", maximum=512)
        parsed = urlsplit(rendered)
        if parsed.scheme not in _ALLOWED_ENDPOINT_SCHEMES:
            allowed = ", ".join(sorted(_ALLOWED_ENDPOINT_SCHEMES))
            raise ValueError(f"sink.endpoint_url scheme must be one of: {allowed}")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("sink.endpoint_url must not include credentials")
        if not parsed.netloc or parsed.hostname is None:
            raise ValueError("sink.endpoint_url must include a host")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("sink.endpoint_url must not include path, query, or fragment")
        if parsed.port is not None and (parsed.port < 1 or parsed.port > _MAX_TCP_PORT):
            raise ValueError("sink.endpoint_url port must be between 1 and 65535")
        return rendered

    @field_validator("region_name")
    @classmethod
    def validate_region_name(cls, value: str | None) -> str | None:
        """Validate optional non-secret region names."""

        rendered = _validate_optional_text(value, field="sink.region_name", maximum=64)
        if rendered is not None and not _REGION_RE.fullmatch(rendered):
            raise ValueError("sink.region_name contains unsupported characters")
        return rendered

    @field_validator("profile_name")
    @classmethod
    def validate_profile_name(cls, value: str | None) -> str | None:
        """Validate optional SDK profile names."""

        rendered = _validate_optional_text(value, field="sink.profile_name", maximum=128)
        if rendered is not None and not _PROFILE_RE.fullmatch(rendered):
            raise ValueError("sink.profile_name contains unsupported characters")
        return rendered

    @field_validator(
        "aws_access_key_id_env",
        "aws_secret_access_key_env",
        "aws_session_token_env",
    )
    @classmethod
    def validate_credential_env(cls, value: str | None, info: object) -> str | None:
        """Validate environment variable names used for credentials."""

        field_name = getattr(info, "field_name", "credential_env")
        return _validate_optional_env(value, field=f"sink.{field_name}")

    @field_validator("key_prefix")
    @classmethod
    def validate_key_prefix(cls, value: str | None) -> str | None:
        """Validate optional non-secret key namespaces."""

        rendered = _validate_optional_text(value, field="sink.key_prefix", maximum=128)
        if rendered is not None and not _SAFE_SEGMENT_RE.fullmatch(rendered):
            raise ValueError(
                "sink.key_prefix must start with a letter or digit and contain only "
                "letters, digits, '_', '.', '=', or '-'"
            )
        return rendered

    @field_validator("object_suffix", "sidecar_suffix")
    @classmethod
    def validate_object_suffix(cls, value: str, info: object) -> str:
        """Validate bounded object suffixes."""

        field_name = getattr(info, "field_name", "object_suffix")
        rendered = _validate_plain_text(value, field=f"sink.{field_name}", maximum=32)
        if "/" in rendered or "\\" in rendered or not _OBJECT_SUFFIX_RE.fullmatch(rendered):
            raise ValueError(f"sink.{field_name} must be a simple extension such as .json")
        return rendered

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        """Validate static content types without accepting parameters."""

        rendered = _validate_plain_text(value, field="sink.content_type", maximum=128)
        if rendered != "application/json":
            raise ValueError("sink.content_type currently supports only application/json")
        return rendered

    @field_validator("object_metadata")
    @classmethod
    def validate_object_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate low-cardinality static object metadata."""

        if len(value) > _MAX_METADATA_ITEMS:
            raise ValueError(f"sink.object_metadata supports at most {_MAX_METADATA_ITEMS} items")
        normalized: dict[str, str] = {}
        seen: set[str] = set()
        for raw_name, raw_value in value.items():
            if not isinstance(raw_name, str) or not isinstance(raw_value, str):
                raise ValueError("sink.object_metadata must map strings to strings")
            name = _validate_plain_text(raw_name, field="sink.object_metadata name", maximum=64)
            if not _METADATA_NAME_RE.fullmatch(name):
                raise ValueError(
                    "sink.object_metadata names must contain only letters, digits, '_', '.', or '-'"
                )
            key = name.casefold()
            if key in seen:
                raise ValueError(f"sink.object_metadata contains duplicate key {name!r}")
            seen.add(key)
            if key in {"authorization", "cookie", "x-api-key", "x-auth-token"}:
                raise ValueError("sink.object_metadata must not contain secret-bearing names")
            normalized[name] = _validate_plain_text(
                raw_value,
                field=f"sink.object_metadata.{name}",
                maximum=256,
            )
        return normalized

    @model_validator(mode="after")
    def validate_cross_field_policy(self) -> S3SinkConfig:
        """Validate credential, endpoint, sidecar, and compression combinations."""

        if self.endpoint_url is not None:
            parsed = urlsplit(self.endpoint_url)
            if parsed.scheme == "http" and not (
                self.allow_http_for_local_testing
                and parsed.hostname is not None
                and _is_loopback_host(parsed.hostname)
            ):
                raise ValueError(
                    "sink.endpoint_url must use https unless allow_http_for_local_testing "
                    "is true and the host is loopback"
                )

        has_env_credentials = any(
            (
                self.aws_access_key_id_env,
                self.aws_secret_access_key_env,
                self.aws_session_token_env,
            )
        )
        if self.credential_mode == "environment":
            if not self.aws_access_key_id_env or not self.aws_secret_access_key_env:
                raise ValueError(
                    "sink.credential_mode='environment' requires aws_access_key_id_env "
                    "and aws_secret_access_key_env"
                )
            if self.profile_name is not None:
                raise ValueError("sink.profile_name is not valid with environment credentials")
        elif has_env_credentials:
            raise ValueError(
                "aws credential environment references require sink.credential_mode='environment'"
            )

        if self.credential_mode == "profile" and self.profile_name is None:
            raise ValueError("sink.credential_mode='profile' requires sink.profile_name")
        if self.credential_mode != "profile" and self.profile_name is not None:
            raise ValueError("sink.profile_name requires sink.credential_mode='profile'")

        if self.metadata_mode == "sidecar" and self.sidecar_suffix == self.object_suffix:
            raise ValueError("sink.sidecar_suffix must differ from sink.object_suffix")
        if self.metadata_mode == "sidecar" and self.duplicate_policy == "fail_existing":
            raise ValueError(
                "sink.metadata_mode='sidecar' is not safe with duplicate_policy='fail_existing'"
            )
        if self.compression == "gzip" and self.object_suffix == ".json":
            raise ValueError("sink.compression='gzip' requires a compressed object suffix")
        if self.retry_max_backoff_ms < self.retry_backoff_ms:
            raise ValueError("sink.retry_max_backoff_ms must be greater than retry_backoff_ms")
        return self
