# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration for the first-party Oracle Coherence CE sink.

The Oracle Coherence Community Edition sink treats its JSON configuration as a
trust boundary.  It accepts only a small, reviewed set of driver options and
rejects ambiguous cache names, overwrite policies, serializer modes, and value
limits before any client session is opened.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.payload import PayloadStorageMode

CoherenceStorageKind = Literal["cache", "map"]
CoherenceKeyStrategy = Literal[
    "idempotency_key",
    "stream_sequence",
    "message_id",
    "payload_sha256",
]
CoherenceDuplicatePolicy = Literal["skip_existing", "replace", "fail_existing"]
CoherenceSerializer = Literal["json"]
CoherenceDurabilityMode = Literal["operator_confirmed"]

_ADDRESS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,253}:\d{1,5}$")
_CACHE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_KEY_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ASCII_CONTROL_CUTOFF = 32
_ASCII_DELETE = 127
_MAX_TCP_PORT = 65_535


def _contains_control_characters(value: str) -> bool:
    """Return true when a configuration value contains ASCII controls."""

    return any(
        ord(character) < _ASCII_CONTROL_CUTOFF or ord(character) == _ASCII_DELETE
        for character in value
    )


def _validate_public_text(value: str, *, field: str, maximum: int) -> str:
    """Validate bounded, non-secret text values used by the Coherence driver."""

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


class CoherenceSinkConfig(BaseModel):
    """Validated configuration for ``CoherenceSink``.

    ``durability`` is intentionally explicit.  The sink can only prove that the
    Coherence client accepted the write operation.  Operators must decide
    whether the configured Coherence cluster policy is durable enough for
    ACK-gated custody in their environment.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["coherence"] = "coherence"
    address: str = "127.0.0.1:1408"
    scope: str = ""
    cache_name: str = "nats_sinks_events"
    storage: CoherenceStorageKind = "cache"
    serializer: CoherenceSerializer = "json"
    key_strategy: CoherenceKeyStrategy = "idempotency_key"
    key_prefix: str | None = None
    duplicate_policy: CoherenceDuplicatePolicy = "skip_existing"
    payload_mode: PayloadStorageMode = "json_or_envelope"
    ttl_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    max_key_bytes: int = Field(default=512, ge=64, le=4096)
    max_value_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    ready_timeout_seconds: float = Field(default=30.0, ge=0, le=300)
    session_disconnect_seconds: float = Field(default=30.0, ge=0, le=300)
    durability: CoherenceDurabilityMode = "operator_confirmed"

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        """Validate the Coherence gRPC host and port without accepting URLs."""

        rendered = _validate_public_text(value, field="sink.address", maximum=260)
        if "://" in rendered or "@" in rendered or not _ADDRESS_RE.fullmatch(rendered):
            raise ValueError("sink.address must be a host:port value without scheme or userinfo")
        _, port_text = rendered.rsplit(":", maxsplit=1)
        port = int(port_text)
        if port < 1 or port > _MAX_TCP_PORT:
            raise ValueError("sink.address port must be between 1 and 65535")
        return rendered

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        """Validate the optional Coherence scope name."""

        if value == "":
            return value
        return _validate_public_text(value, field="sink.scope", maximum=128)

    @field_validator("cache_name")
    @classmethod
    def validate_cache_name(cls, value: str) -> str:
        """Validate map/cache names before they reach the client."""

        rendered = _validate_public_text(value, field="sink.cache_name", maximum=128)
        if not _CACHE_NAME_RE.fullmatch(rendered):
            raise ValueError(
                "sink.cache_name must contain only letters, numbers, dots, underscores, "
                "or hyphens and must start with a letter or number"
            )
        return rendered

    @field_validator("key_prefix")
    @classmethod
    def validate_key_prefix(cls, value: str | None) -> str | None:
        """Validate optional key prefixes used in persisted keys."""

        if value is None:
            return None
        rendered = _validate_public_text(value, field="sink.key_prefix", maximum=128)
        if not _KEY_PREFIX_RE.fullmatch(rendered):
            raise ValueError(
                "sink.key_prefix must contain only letters, numbers, dots, underscores, "
                "colons, or hyphens and must start with a letter or number"
            )
        return rendered

    @model_validator(mode="after")
    def validate_storage_shape(self) -> CoherenceSinkConfig:
        """Reject TTL configuration that the selected Coherence type cannot use."""

        if self.storage == "map" and self.ttl_seconds is not None:
            raise ValueError("sink.ttl_seconds is only supported when sink.storage is 'cache'")
        return self
