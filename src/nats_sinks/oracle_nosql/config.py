# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration for the first-party Oracle NoSQL Database sink.

The Oracle NoSQL Database sink accepts destination and table metadata from JSON
configuration, which is a trust boundary.  It therefore validates endpoints,
table identifiers, key fields, value fields, duplicate policies, auth modes,
and table creation controls before any SDK handle is opened.
"""

from __future__ import annotations

import re
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.payload import PayloadStorageMode

OracleNoSqlDeploymentMode = Literal["kvstore", "cloudsim", "cloud"]
OracleNoSqlAuthMode = Literal[
    "store_access_token",
    "cloudsim",
    "oci_config_file",
    "instance_principal",
]
OracleNoSqlKeyStrategy = Literal[
    "idempotency_key",
    "stream_sequence",
    "message_id",
    "payload_sha256",
]
OracleNoSqlDuplicatePolicy = Literal["skip_existing", "replace", "fail_existing"]
OracleNoSqlDurabilityMode = Literal["operator_confirmed"]

_ASCII_CONTROL_CUTOFF = 32
_ASCII_DELETE = 127
_MAX_TCP_PORT = 65_535
_HOST_PORT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,253}:\d{1,5}$")
_TABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}(?:\.[A-Za-z][A-Za-z0-9_]{0,127})?$")
_FIELD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_KEY_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_OCI_PROFILE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ALLOWED_ENDPOINT_SCHEMES = frozenset({"http", "https"})


def _contains_control_characters(value: str) -> bool:
    """Return true when a string contains unsafe ASCII controls."""

    return any(
        ord(character) < _ASCII_CONTROL_CUTOFF or ord(character) == _ASCII_DELETE
        for character in value
    )


def _validate_plain_text(value: str, *, field: str, maximum: int) -> str:
    """Validate bounded, non-secret configuration text."""

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


def _validate_optional_path(value: str | None, *, field: str) -> str | None:
    """Validate optional local SDK path strings without reading them."""

    if value is None:
        return None
    return _validate_plain_text(value, field=field, maximum=1024)


def _validate_optional_env(value: str | None, *, field: str) -> str | None:
    """Validate optional environment variable names used for secrets."""

    if value is None:
        return None
    rendered = _validate_plain_text(value, field=field, maximum=128)
    if not _ENV_NAME_RE.fullmatch(rendered):
        raise ValueError(
            f"{field} must be an uppercase environment variable name using letters, "
            "numbers, and underscores"
        )
    return rendered


def _validate_host_port(value: str, *, field: str) -> str:
    """Validate SDK host:port endpoints without schemes or userinfo."""

    rendered = _validate_plain_text(value, field=field, maximum=260)
    if "://" in rendered or "@" in rendered or not _HOST_PORT_RE.fullmatch(rendered):
        raise ValueError(f"{field} must be host:port or an http(s) URL without userinfo")
    _, port_text = rendered.rsplit(":", maxsplit=1)
    port = int(port_text)
    if port < 1 or port > _MAX_TCP_PORT:
        raise ValueError(f"{field} port must be between 1 and 65535")
    return rendered


class OracleNoSqlSinkConfig(BaseModel):
    """Validated configuration for ``OracleNoSqlSink``.

    ``durability`` is deliberately explicit.  The sink can prove only that the
    Oracle NoSQL SDK accepted the write or conditional write operation.  The
    operator remains responsible for reviewing the Oracle NoSQL Database store,
    proxy, replication, backup, and regional consistency posture before using
    it as an ACK-gated custody target.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["oracle_nosql"] = "oracle_nosql"
    endpoint: str = "127.0.0.1:8080"
    deployment_mode: OracleNoSqlDeploymentMode = "kvstore"
    auth_mode: OracleNoSqlAuthMode | None = None
    table_name: str = "nats_sinks_events"
    key_field: str = "sink_key"
    value_field: str = "event_json"
    stored_at_field: str = "stored_at_epoch_ns"
    namespace: str | None = None
    compartment_id: str | None = None
    cloudsim_tenant_id: str = "cloudsim"
    oci_config_file: str | None = None
    oci_profile: str = "DEFAULT"
    oci_private_key_passphrase_env: str | None = None
    key_strategy: OracleNoSqlKeyStrategy = "idempotency_key"
    key_prefix: str | None = None
    duplicate_policy: OracleNoSqlDuplicatePolicy = "skip_existing"
    payload_mode: PayloadStorageMode = "json_or_envelope"
    auto_create: bool = False
    read_units: int = Field(default=10, ge=1, le=50_000)
    write_units: int = Field(default=10, ge=1, le=50_000)
    storage_gb: int = Field(default=1, ge=1, le=1024)
    table_timeout_ms: int = Field(default=50_000, ge=1_000, le=600_000)
    table_poll_interval_ms: int = Field(default=3_000, ge=100, le=60_000)
    max_key_bytes: int = Field(default=512, ge=64, le=4096)
    max_value_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    durability: OracleNoSqlDurabilityMode = "operator_confirmed"

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        """Validate SDK endpoints without accepting credentials or paths."""

        rendered = _validate_plain_text(value, field="sink.endpoint", maximum=512)
        if "://" not in rendered:
            return _validate_host_port(rendered, field="sink.endpoint")
        parsed = urlsplit(rendered)
        if parsed.scheme not in _ALLOWED_ENDPOINT_SCHEMES:
            allowed = ", ".join(sorted(_ALLOWED_ENDPOINT_SCHEMES))
            raise ValueError(f"sink.endpoint URL scheme must be one of: {allowed}")
        if not parsed.netloc:
            raise ValueError("sink.endpoint URL must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("sink.endpoint must not include credentials")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("sink.endpoint URL must not include path, query, or fragment")
        if parsed.port is not None and (parsed.port < 1 or parsed.port > _MAX_TCP_PORT):
            raise ValueError("sink.endpoint port must be between 1 and 65535")
        return rendered

    @field_validator("table_name")
    @classmethod
    def validate_table_name(cls, value: str) -> str:
        """Validate Oracle NoSQL table names before SDK request construction."""

        rendered = _validate_plain_text(value, field="sink.table_name", maximum=257)
        if not _TABLE_NAME_RE.fullmatch(rendered):
            raise ValueError(
                "sink.table_name must contain one or two dot-separated identifiers; "
                "each identifier must start with a letter and contain only letters, "
                "numbers, or underscores"
            )
        return rendered

    @field_validator("key_field", "value_field", "stored_at_field")
    @classmethod
    def validate_field_name(cls, value: str, info: object) -> str:
        """Validate configured Oracle NoSQL record field names."""

        field_name = getattr(info, "field_name", "field")
        rendered = _validate_plain_text(value, field=f"sink.{field_name}", maximum=128)
        if not _FIELD_NAME_RE.fullmatch(rendered):
            raise ValueError(
                f"sink.{field_name} must start with a letter and contain only letters, "
                "numbers, or underscores"
            )
        return rendered

    @field_validator("namespace", "compartment_id")
    @classmethod
    def validate_optional_public_text(cls, value: str | None, info: object) -> str | None:
        """Validate optional cloud namespace and compartment strings."""

        if value is None:
            return None
        field_name = getattr(info, "field_name", "field")
        return _validate_plain_text(value, field=f"sink.{field_name}", maximum=512)

    @field_validator("cloudsim_tenant_id")
    @classmethod
    def validate_cloudsim_tenant_id(cls, value: str) -> str:
        """Validate the non-secret Cloud Simulator namespace token."""

        return _validate_plain_text(value, field="sink.cloudsim_tenant_id", maximum=128)

    @field_validator("oci_config_file")
    @classmethod
    def validate_oci_config_file(cls, value: str | None) -> str | None:
        """Validate the optional OCI SDK config-file path."""

        return _validate_optional_path(value, field="sink.oci_config_file")

    @field_validator("oci_profile")
    @classmethod
    def validate_oci_profile(cls, value: str) -> str:
        """Validate the OCI profile name used by the SDK provider."""

        rendered = _validate_plain_text(value, field="sink.oci_profile", maximum=128)
        if not _OCI_PROFILE_RE.fullmatch(rendered):
            raise ValueError(
                "sink.oci_profile must start with a letter and contain only letters, "
                "numbers, dots, underscores, colons, or hyphens"
            )
        return rendered

    @field_validator("oci_private_key_passphrase_env")
    @classmethod
    def validate_passphrase_env(cls, value: str | None) -> str | None:
        """Validate the env-var name for an OCI private-key passphrase."""

        return _validate_optional_env(value, field="sink.oci_private_key_passphrase_env")

    @field_validator("key_prefix")
    @classmethod
    def validate_key_prefix(cls, value: str | None) -> str | None:
        """Validate optional key prefixes used in persisted NoSQL keys."""

        if value is None:
            return None
        rendered = _validate_plain_text(value, field="sink.key_prefix", maximum=128)
        if not _KEY_PREFIX_RE.fullmatch(rendered):
            raise ValueError(
                "sink.key_prefix must contain only letters, numbers, dots, underscores, "
                "colons, or hyphens and must start with a letter or number"
            )
        return rendered

    @model_validator(mode="after")
    def validate_auth_and_table_shape(self) -> OracleNoSqlSinkConfig:
        """Apply deployment-specific auth defaults and table field checks."""

        effective_auth = self.auth_mode
        if self.auth_mode is None:
            effective_auth = cast(
                OracleNoSqlAuthMode,
                {
                    "kvstore": "store_access_token",
                    "cloudsim": "cloudsim",
                    "cloud": "oci_config_file",
                }[self.deployment_mode],
            )
            self.auth_mode = effective_auth

        allowed_auth = {
            "kvstore": {"store_access_token"},
            "cloudsim": {"cloudsim"},
            "cloud": {"oci_config_file", "instance_principal"},
        }[self.deployment_mode]
        if effective_auth not in allowed_auth:
            allowed = ", ".join(sorted(allowed_auth))
            raise ValueError(
                f"sink.auth_mode {effective_auth!r} is not valid for "
                f"sink.deployment_mode {self.deployment_mode!r}; allowed: {allowed}"
            )
        field_names = [self.key_field, self.value_field, self.stored_at_field]
        if len(set(field_names)) != len(field_names):
            raise ValueError("sink key_field, value_field, and stored_at_field must be distinct")
        return self
