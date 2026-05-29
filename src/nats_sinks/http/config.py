# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration models for the first-party HTTP sink.

The HTTP sink is an outbound egress boundary.  Configuration therefore stays
deliberately narrow: operators choose one fixed endpoint, a small allow-list of
methods and headers, bounded timeouts, and explicit idempotency behavior.
Messages can influence only the request body and the generated idempotency
value; they can never choose the destination URL, method, headers, or retry
policy.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.payload import PayloadStorageMode

HttpMethod = Literal["POST", "PUT", "PATCH"]
HttpBodyFormat = Literal["envelope", "payload"]
HttpIdempotencyStrategy = Literal[
    "idempotency_key",
    "stream_sequence",
    "message_id",
    "payload_sha256",
]
HttpRetryBackoffMode = Literal["fixed", "linear", "exponential"]
HttpRetryJitterMode = Literal["none", "full", "equal"]

_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_CONTROL_CHARACTER_CUTOFF = 32
_ASCII_DELETE = 127
_MAX_URL_PATH_LENGTH = 2048
_MAX_HEADER_VALUE_LENGTH = 2048
_MAX_HEADERS = 32
_MAX_STATUS_CODES = 64
_MIN_HTTP_STATUS = 100
_MAX_HTTP_STATUS = 599
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_FORBIDDEN_HEADER_NAMES = frozenset(
    {
        "accept",
        "connection",
        "content-length",
        "content-type",
        "host",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "upgrade",
        "user-agent",
    }
)
_DIRECT_SECRET_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
    }
)


def _contains_control_characters(value: str) -> bool:
    """Return true when public text contains control characters."""

    return any(
        ord(character) < _CONTROL_CHARACTER_CUTOFF or ord(character) == _ASCII_DELETE
        for character in value
    )


def _validate_header_name(name: object, *, field: str) -> str:
    """Validate and normalize an HTTP header name."""

    if not isinstance(name, str):
        raise ValueError(f"{field} header names must be strings")
    rendered = name.strip()
    if rendered != name or not _HEADER_NAME_RE.fullmatch(rendered):
        raise ValueError(f"{field} header names must be RFC token strings without padding")
    lowered = rendered.casefold()
    if lowered in _FORBIDDEN_HEADER_NAMES:
        raise ValueError(f"{field} must not configure framework-owned header {rendered!r}")
    return rendered


def _validate_public_header_value(value: object, *, field: str) -> str:
    """Validate a direct non-secret HTTP header value."""

    if not isinstance(value, str):
        raise ValueError(f"{field} header values must be strings")
    if value != value.strip() or not value:
        raise ValueError(f"{field} header values must not be blank or padded")
    if len(value) > _MAX_HEADER_VALUE_LENGTH:
        raise ValueError(
            f"{field} header values must be at most {_MAX_HEADER_VALUE_LENGTH} characters"
        )
    if _contains_control_characters(value):
        raise ValueError(f"{field} header values must not contain control characters")
    return value


def _validate_env_name(value: object, *, field: str) -> str:
    """Validate an environment-variable reference without reading the secret."""

    if not isinstance(value, str) or value != value.strip() or not _ENV_NAME_RE.fullmatch(value):
        raise ValueError(
            f"{field} environment variable names must use uppercase letters, numbers, "
            "and underscores"
        )
    return value


def _normalize_header_mapping(
    value: object,
    *,
    field: str,
    allow_secret_header_names: bool,
) -> dict[str, str]:
    """Validate a configured HTTP header mapping."""

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    if len(value) > _MAX_HEADERS:
        raise ValueError(f"{field} supports at most {_MAX_HEADERS} entries")
    rendered: dict[str, str] = {}
    seen: set[str] = set()
    for raw_name, raw_value in value.items():
        name = _validate_header_name(raw_name, field=field)
        lowered = name.casefold()
        if lowered in seen:
            raise ValueError(f"{field} contains duplicate header {name!r}")
        if not allow_secret_header_names and lowered in _DIRECT_SECRET_HEADER_NAMES:
            raise ValueError(f"{field} must not contain sensitive header {name!r}; use headers_env")
        seen.add(lowered)
        rendered[name] = (
            _validate_env_name(raw_value, field=field)
            if allow_secret_header_names
            else _validate_public_header_value(raw_value, field=field)
        )
    return rendered


def _validate_url(
    value: str,
    *,
    allow_http_for_local_testing: bool,
    allowed_hosts: tuple[str, ...],
) -> str:
    """Validate the static HTTP destination URL."""

    rendered = value.strip()
    if not rendered:
        raise ValueError("sink.url must not be empty")
    if rendered != value or _contains_control_characters(rendered):
        raise ValueError("sink.url must not contain padding or control characters")

    parsed = urlsplit(rendered)
    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if scheme not in {"https", "http"}:
        raise ValueError("sink.url must use https")
    if scheme == "http" and not (allow_http_for_local_testing and host in _LOOPBACK_HOSTS):
        raise ValueError("sink.url must use https outside local loopback tests")
    if not host:
        raise ValueError("sink.url must include a host")
    if parsed.username or parsed.password:
        raise ValueError("sink.url must not include userinfo")
    if parsed.fragment:
        raise ValueError("sink.url must not include a fragment")
    if parsed.query:
        raise ValueError("sink.url must not include a query string")
    if len(parsed.path) > _MAX_URL_PATH_LENGTH:
        raise ValueError("sink.url path is too long")
    if allowed_hosts and host not in allowed_hosts:
        raise ValueError("sink.url host is not in endpoint_allowed_hosts")
    return rendered


class HttpIdempotencyConfig(BaseModel):
    """HTTP idempotency-key propagation settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    required: bool = True
    header: str = "Idempotency-Key"
    strategy: HttpIdempotencyStrategy = "idempotency_key"

    @field_validator("header")
    @classmethod
    def validate_header(cls, value: str) -> str:
        """Validate the configured idempotency-key header name."""

        return _validate_header_name(value, field="idempotency.header")

    @model_validator(mode="after")
    def validate_enabled_policy(self) -> HttpIdempotencyConfig:
        """Reject contradictory idempotency settings."""

        if not self.enabled and self.required:
            raise ValueError("idempotency.required requires idempotency.enabled=true")
        return self


class HttpRetryConfig(BaseModel):
    """Bounded in-call HTTP retry settings.

    The default does not retry inside one sink write.  JetStream redelivery
    remains the primary retry mechanism unless an operator explicitly opts into
    bounded HTTP retries for an idempotent endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=0, ge=0, le=10)
    backoff_ms: int = Field(default=250, ge=0, le=60_000)
    max_backoff_ms: int = Field(default=5_000, ge=0, le=300_000)
    backoff_mode: HttpRetryBackoffMode = "exponential"
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    jitter: HttpRetryJitterMode = "full"

    @model_validator(mode="after")
    def validate_backoff_bounds(self) -> HttpRetryConfig:
        """Keep retry delays bounded and internally consistent."""

        if self.max_backoff_ms < self.backoff_ms:
            raise ValueError("retry.max_backoff_ms must be at least retry.backoff_ms")
        return self


class HttpSinkConfig(BaseModel):
    """Validated configuration for ``HttpSink``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http"] = "http"
    url: str
    method: HttpMethod = "POST"
    body_format: HttpBodyFormat = "envelope"
    endpoint_allowed_hosts: tuple[str, ...] = Field(default_factory=tuple)
    allow_http_for_local_testing: bool = False
    headers: dict[str, str] = Field(default_factory=dict)
    headers_env: dict[str, str] = Field(default_factory=dict)
    user_agent: str = "nats-sinks-http/0.4"
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_request_bytes: int = Field(default=1_048_576, ge=128, le=52_428_800)
    max_response_bytes: int = Field(default=65_536, ge=0, le=1_048_576)
    success_statuses: tuple[int, ...] = (200, 201, 202, 204)
    retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504)
    payload_mode: PayloadStorageMode = "json_or_envelope"
    include_metadata: bool = True
    include_mission_metadata: bool = True
    include_security_labels: bool = True
    include_custody: bool = True
    idempotency: HttpIdempotencyConfig = Field(default_factory=HttpIdempotencyConfig)
    retry: HttpRetryConfig = Field(default_factory=HttpRetryConfig)

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: object) -> str:
        """Normalize HTTP method names before allow-list validation."""

        if not isinstance(value, str):
            raise ValueError("sink.method must be a string")
        return value.strip().upper()

    @field_validator("endpoint_allowed_hosts", mode="before")
    @classmethod
    def normalize_endpoint_allowed_hosts(cls, value: object) -> tuple[str, ...]:
        """Normalize the optional destination host allow-list."""

        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("endpoint_allowed_hosts must be a list of hostnames")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("endpoint_allowed_hosts entries must be strings")
            host = item.strip().casefold()
            if not host:
                raise ValueError("endpoint_allowed_hosts entries must not be empty")
            if host != item.casefold() or _contains_control_characters(item):
                raise ValueError(
                    "endpoint_allowed_hosts entries must not contain padding or control characters"
                )
            if "/" in host or "@" in host:
                raise ValueError("endpoint_allowed_hosts entries must be hostnames only")
            if host in seen:
                raise ValueError(f"endpoint_allowed_hosts contains duplicate host {host!r}")
            seen.add(host)
            normalized.append(host)
        return tuple(normalized)

    @field_validator("headers", mode="before")
    @classmethod
    def normalize_headers(cls, value: object) -> dict[str, str]:
        """Validate static non-secret request headers."""

        return _normalize_header_mapping(
            value,
            field="headers",
            allow_secret_header_names=False,
        )

    @field_validator("headers_env", mode="before")
    @classmethod
    def normalize_headers_env(cls, value: object) -> dict[str, str]:
        """Validate environment-backed request headers."""

        return _normalize_header_mapping(
            value,
            field="headers_env",
            allow_secret_header_names=True,
        )

    @field_validator("user_agent")
    @classmethod
    def validate_user_agent(cls, value: str) -> str:
        """Validate the generated User-Agent header."""

        if value != value.strip() or not value:
            raise ValueError("user_agent must not be blank or padded")
        if len(value) > _MAX_HEADER_VALUE_LENGTH or _contains_control_characters(value):
            raise ValueError("user_agent must be bounded text without control characters")
        return value

    @field_validator("success_statuses", "retry_statuses", mode="before")
    @classmethod
    def normalize_statuses(cls, value: object, info: object) -> tuple[int, ...]:
        """Validate configured HTTP status allow-lists."""

        field_name = getattr(info, "field_name", "statuses")
        if not isinstance(value, list | tuple):
            raise ValueError(f"{field_name} must be a list of HTTP status codes")
        if not value:
            raise ValueError(f"{field_name} must not be empty")
        if len(value) > _MAX_STATUS_CODES:
            raise ValueError(f"{field_name} supports at most {_MAX_STATUS_CODES} status codes")
        statuses: list[int] = []
        seen: set[int] = set()
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(f"{field_name} entries must be integers")
            if item < _MIN_HTTP_STATUS or item > _MAX_HTTP_STATUS:
                raise ValueError(f"{field_name} entries must be HTTP status codes")
            if item in seen:
                raise ValueError(f"{field_name} contains duplicate status {item}")
            seen.add(item)
            statuses.append(item)
        return tuple(statuses)

    @model_validator(mode="after")
    def validate_endpoint_and_headers(self) -> HttpSinkConfig:
        """Validate cross-field URL, status, and header rules."""

        self.url = _validate_url(
            self.url,
            allow_http_for_local_testing=self.allow_http_for_local_testing,
            allowed_hosts=self.endpoint_allowed_hosts,
        )
        duplicate_headers = {
            left.casefold()
            for left in self.headers
            for right in self.headers_env
            if left.casefold() == right.casefold()
        }
        if duplicate_headers:
            joined = ", ".join(sorted(duplicate_headers))
            raise ValueError(
                f"headers and headers_env must not configure the same header: {joined}"
            )
        status_overlap = sorted(set(self.success_statuses) & set(self.retry_statuses))
        if status_overlap:
            joined = ", ".join(str(status) for status in status_overlap)
            raise ValueError(f"success_statuses and retry_statuses overlap: {joined}")
        return self
