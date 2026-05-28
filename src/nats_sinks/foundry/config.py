# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration for the experimental Palantir Foundry sink.

Foundry deployments are customer-specific, so this first connector increment
targets the public Streams push-ingestion shape through an explicit HTTP client
boundary.  The configuration validates endpoint shape, authentication posture,
field names, and size limits before any message is written.

Secrets are referenced only by environment-variable name.  Inline tokens,
client secrets, tenant URLs in logs, and arbitrary SDK options are deliberately
not part of the model.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.payload import PayloadStorageMode

FoundryTarget = Literal["stream"]
FoundryAuthMode = Literal["bearer_token_env", "oauth2_client_credentials"]
FoundryRecordKeyStrategy = Literal[
    "idempotency_key",
    "stream_sequence",
    "message_id",
    "payload_sha256",
]
FoundryRecordWrapper = Literal["value"]

_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CONTROL_CHARACTER_CUTOFF = 32
_ASCII_DELETE = 127
_MAX_URL_PATH_LENGTH = 2048
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _contains_control_characters(value: str) -> bool:
    """Return true when a public config string contains control characters."""

    return any(
        ord(character) < _CONTROL_CHARACTER_CUTOFF or ord(character) == _ASCII_DELETE
        for character in value
    )


def _validate_env_name(value: str | None, *, field: str) -> str | None:
    """Validate secret environment-variable names without reading the secret."""

    if value is None:
        return None
    if value != value.strip() or not _ENV_NAME_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be an uppercase environment variable name using letters, "
            "numbers, and underscores"
        )
    return value


def _validate_field_name(value: str, *, field: str) -> str:
    """Validate a Foundry record field name used for generated JSON values."""

    if value != value.strip() or not _FIELD_NAME_RE.fullmatch(value):
        raise ValueError(
            f"{field} must start with a letter or underscore and contain only "
            "letters, numbers, and underscores"
        )
    return value


def _validate_public_text(value: str | None, *, field: str) -> str | None:
    """Validate optional public text fields that may reach HTTP requests."""

    if value is None:
        return None
    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field} must not be empty")
    if rendered != value or _contains_control_characters(value):
        raise ValueError(f"{field} must not contain padding or control characters")
    return value


def _validate_url(
    value: str,
    *,
    field: str,
    allow_http_for_local_testing: bool,
    allowed_hosts: tuple[str, ...],
) -> str:
    """Validate a Foundry URL without exposing its value in errors."""

    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field} must not be empty")
    if rendered != value or _contains_control_characters(value):
        raise ValueError(f"{field} must not contain padding or control characters")

    parsed = urlsplit(rendered)
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    if scheme not in {"https", "http"}:
        raise ValueError(f"{field} must use https")
    if scheme == "http" and not (allow_http_for_local_testing and host in _LOOPBACK_HOSTS):
        raise ValueError(f"{field} must use https outside local loopback tests")
    if not host:
        raise ValueError(f"{field} must include a host")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not include userinfo")
    if parsed.fragment:
        raise ValueError(f"{field} must not include a fragment")
    if parsed.query:
        raise ValueError(f"{field} must not include a query string")
    if len(parsed.path) > _MAX_URL_PATH_LENGTH:
        raise ValueError(f"{field} path is too long")

    if allowed_hosts and host not in allowed_hosts:
        raise ValueError(f"{field} host is not in endpoint_allowed_hosts")
    return rendered


class FoundrySinkConfig(BaseModel):
    """Validated configuration for ``FoundrySink``.

    The connector currently supports Foundry Streams push ingestion.  It is
    marked experimental until a maintainer runs the optional live certification
    path against an approved Foundry environment.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["foundry"] = "foundry"
    target: FoundryTarget = "stream"
    stream_push_url: str
    auth_mode: FoundryAuthMode = "bearer_token_env"
    bearer_token_env: str | None = None
    oauth2_token_url: str | None = None
    oauth2_client_id_env: str | None = None
    oauth2_client_secret_env: str | None = None
    oauth2_scope: str | None = None
    endpoint_allowed_hosts: tuple[str, ...] = Field(default_factory=tuple)
    allow_http_for_local_testing: bool = False
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=10)
    batch_size: int = Field(default=100, ge=1, le=1000)
    max_record_bytes: int = Field(default=262_144, ge=1024, le=10_485_760)
    max_batch_bytes: int = Field(default=4_194_304, ge=1024, le=52_428_800)
    max_response_bytes: int = Field(default=65_536, ge=0, le=1_048_576)
    payload_mode: PayloadStorageMode = "json_or_envelope"
    include_metadata: bool = True
    include_mission_metadata: bool = True
    include_security_labels: bool = True
    include_custody: bool = True
    record_key_strategy: FoundryRecordKeyStrategy = "idempotency_key"
    record_wrapper: FoundryRecordWrapper = "value"
    record_key_field: str = "nats_sinks_record_key"
    subject_field: str = "subject"
    payload_field: str = "payload"
    payload_info_field: str = "payload_info"
    metadata_field: str = "metadata"
    priority_field: str = "priority"
    classification_field: str = "classification"
    labels_field: str = "labels"
    labels_list_field: str = "labels_list"
    mission_metadata_field: str = "mission_metadata"
    security_labels_field: str = "security_labels"
    custody_field: str = "custody"

    @field_validator(
        "bearer_token_env",
        "oauth2_client_id_env",
        "oauth2_client_secret_env",
    )
    @classmethod
    def validate_env_names(cls, value: str | None, info: object) -> str | None:
        """Validate configured environment-variable names."""

        field_name = getattr(getattr(info, "field_name", None), "__str__", lambda: "field")()
        return _validate_env_name(value, field=field_name)

    @field_validator("endpoint_allowed_hosts", mode="before")
    @classmethod
    def normalize_endpoint_allowed_hosts(cls, value: object) -> tuple[str, ...]:
        """Normalize the optional endpoint host allow-list."""

        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("endpoint_allowed_hosts must be a list of hostnames")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("endpoint_allowed_hosts entries must be strings")
            host = item.strip().lower()
            if not host:
                raise ValueError("endpoint_allowed_hosts entries must not be empty")
            if host != item or _contains_control_characters(item):
                raise ValueError(
                    "endpoint_allowed_hosts entries must not contain padding or control characters"
                )
            if "/" in host or "@" in host or ":" in host:
                raise ValueError("endpoint_allowed_hosts entries must be hostnames only")
            if host in seen:
                raise ValueError(f"endpoint_allowed_hosts contains duplicate host {host!r}")
            seen.add(host)
            normalized.append(host)
        return tuple(normalized)

    @field_validator("oauth2_scope")
    @classmethod
    def validate_oauth2_scope(cls, value: str | None) -> str | None:
        """Validate optional OAuth scope text."""

        return _validate_public_text(value, field="oauth2_scope")

    @field_validator(
        "record_key_field",
        "subject_field",
        "payload_field",
        "payload_info_field",
        "metadata_field",
        "priority_field",
        "classification_field",
        "labels_field",
        "labels_list_field",
        "mission_metadata_field",
        "security_labels_field",
        "custody_field",
    )
    @classmethod
    def validate_record_field_names(cls, value: str, info: object) -> str:
        """Validate generated record field names."""

        field_name = getattr(getattr(info, "field_name", None), "__str__", lambda: "field")()
        return _validate_field_name(value, field=field_name)

    @model_validator(mode="after")
    def validate_auth_and_endpoint(self) -> FoundrySinkConfig:
        """Validate cross-field authentication and endpoint posture."""

        _validate_url(
            self.stream_push_url,
            field="stream_push_url",
            allow_http_for_local_testing=self.allow_http_for_local_testing,
            allowed_hosts=self.endpoint_allowed_hosts,
        )
        if self.oauth2_token_url is not None:
            self.oauth2_token_url = _validate_url(
                self.oauth2_token_url,
                field="oauth2_token_url",
                allow_http_for_local_testing=self.allow_http_for_local_testing,
                allowed_hosts=self.endpoint_allowed_hosts,
            )

        if self.auth_mode == "bearer_token_env":
            if self.bearer_token_env is None:
                raise ValueError("bearer_token_env is required for bearer_token_env auth")
            if any(
                value is not None
                for value in (
                    self.oauth2_token_url,
                    self.oauth2_client_id_env,
                    self.oauth2_client_secret_env,
                    self.oauth2_scope,
                )
            ):
                raise ValueError("OAuth2 fields require auth_mode='oauth2_client_credentials'")
        else:
            missing = [
                name
                for name, value in (
                    ("oauth2_token_url", self.oauth2_token_url),
                    ("oauth2_client_id_env", self.oauth2_client_id_env),
                    ("oauth2_client_secret_env", self.oauth2_client_secret_env),
                )
                if value is None
            ]
            if missing:
                joined = ", ".join(missing)
                raise ValueError(f"{joined} required for oauth2_client_credentials auth")
            if self.bearer_token_env is not None:
                raise ValueError("bearer_token_env requires auth_mode='bearer_token_env'")

        field_names = (
            self.record_key_field,
            self.subject_field,
            self.payload_field,
            self.payload_info_field,
            self.metadata_field,
            self.priority_field,
            self.classification_field,
            self.labels_field,
            self.labels_list_field,
            self.mission_metadata_field,
            self.security_labels_field,
            self.custody_field,
        )
        duplicates = sorted({field for field in field_names if field_names.count(field) > 1})
        if duplicates:
            joined = ", ".join(duplicates)
            raise ValueError(f"Foundry record field names must be unique: {joined}")
        if self.max_batch_bytes < self.max_record_bytes:
            raise ValueError("max_batch_bytes must be at least max_record_bytes")
        return self
