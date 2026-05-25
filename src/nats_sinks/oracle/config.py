# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle sink configuration models.

Oracle configuration is validated separately from the generic app config so the
safe sink registry can keep destination-specific options behind the selected
sink type.  The models define write modes, table and column mapping,
connection-pool sizing, optional table creation, and idempotency strategy.
It also models Oracle Autonomous Database connection settings.  Walletless TLS
usually needs only a `tcps` descriptor in `dsn`, while wallet/mTLS deployments
can set `config_dir`, `wallet_location`, and `wallet_password_env`.

Passwords may be supplied directly for embedded tests, but production
configuration should use `password_env` and `wallet_password_env` so the
process resolves secrets from its environment or secret manager at runtime.
Redacted CLI output never prints resolved passwords.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.payload import PayloadStorageMode

OracleWriteMode = Literal["insert", "insert_ignore", "merge", "append"]
IdempotencyStrategy = Literal["stream_sequence", "message_id", "payload_field"]
OracleStagingCleanupMode = Literal["delete_on_success", "keep"]


class OracleIdempotencyConfig(BaseModel):
    """Oracle idempotency configuration."""

    model_config = ConfigDict(extra="forbid")

    strategy: IdempotencyStrategy = "stream_sequence"
    columns: list[str] = Field(default_factory=lambda: ["STREAM_NAME", "STREAM_SEQUENCE"])
    payload_field: str | None = None

    @field_validator("payload_field")
    @classmethod
    def validate_payload_field(cls, value: str | None) -> str | None:
        """Validate dotted payload-field paths before runtime extraction."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("idempotency.payload_field must not be empty")
        if "\x00" in rendered or "\n" in rendered or "\r" in rendered:
            raise ValueError("idempotency.payload_field must not contain control characters")
        parts = rendered.split(".")
        if any(not part for part in parts):
            raise ValueError("idempotency.payload_field must not contain empty path segments")
        return rendered

    @model_validator(mode="after")
    def validate_payload_strategy(self) -> OracleIdempotencyConfig:
        """Validate strategy-specific fields and choose sensible key columns."""

        if self.strategy == "payload_field" and not self.payload_field:
            raise ValueError("idempotency.payload_field is required for payload_field strategy")
        if self.strategy == "message_id" and self.columns == ["STREAM_NAME", "STREAM_SEQUENCE"]:
            self.columns = ["MESSAGE_ID"]
        if self.strategy == "payload_field" and self.columns == ["STREAM_NAME", "STREAM_SEQUENCE"]:
            self.columns = ["MESSAGE_ID"]
        return self


class OracleColumnMapping(BaseModel):
    """Oracle table column mapping."""

    model_config = ConfigDict(extra="forbid")

    stream_name: str = "STREAM_NAME"
    stream_sequence: str = "STREAM_SEQUENCE"
    subject: str = "SUBJECT"
    message_id: str = "MESSAGE_ID"
    priority: str = "PRIORITY"
    classification: str = "CLASSIFICATION"
    labels: str = "LABELS"
    message_created_at_epoch_ns: str = "MESSAGE_CREATED_AT_EPOCH_NS"
    jetstream_timestamp_epoch_ns: str = "JETSTREAM_TIMESTAMP_EPOCH_NS"
    received_at_epoch_ns: str = "RECEIVED_AT_EPOCH_NS"
    stored_at_epoch_ns: str = "STORED_AT_EPOCH_NS"
    payload: str = "PAYLOAD_JSON"
    headers: str = "HEADERS_JSON"
    metadata: str = "METADATA_JSON"
    mission_metadata: str = "MISSION_METADATA_JSON"
    security_labels: str = "SECURITY_LABELS_JSON"


class OracleTableRoute(BaseModel):
    """Route messages matching a NATS subject pattern to a table and policy.

    A route inherits the sink-level idempotency and merge-update policy unless
    it provides explicit overrides.  Keeping route policy inside the validated
    route object lets one OracleSink handle several subject families without
    requiring separate worker processes for every table.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    table: str
    idempotency: OracleIdempotencyConfig | None = None
    merge_update_columns: list[str] | None = None


class OracleStagingConfig(BaseModel):
    """Optional high-throughput staging-table configuration.

    Staging is advanced Oracle behavior and is intentionally disabled by
    default.  When enabled, OracleSink first array-loads normalized rows into a
    validated staging table, then performs one set-based merge into the
    destination table.  The staging table must have the same event columns as
    the destination table plus the configured batch-id column.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    table: str | None = None
    batch_id_column: str = "NATS_SINKS_BATCH_ID"
    cleanup: OracleStagingCleanupMode = "delete_on_success"

    @model_validator(mode="after")
    def validate_staging_table(self) -> OracleStagingConfig:
        """Require explicit staging objects when high-throughput mode is enabled."""

        if self.enabled and not self.table:
            raise ValueError("sink.staging.table is required when sink.staging.enabled is true")
        return self


class OracleSinkConfig(BaseModel):
    """Validated configuration for OracleSink.

    Connection fields intentionally mirror python-oracledb pool options where
    practical.  Keeping them explicit gives operators the ADB/TLS options they
    need without permitting arbitrary driver kwargs from untrusted JSON.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["oracle"] = "oracle"
    dsn: str
    user: str
    password: str | None = None
    password_env: str | None = None
    config_dir: str | None = None
    wallet_location: str | None = None
    wallet_password: str | None = None
    wallet_password_env: str | None = None
    ssl_server_dn_match: bool | None = None
    ssl_server_cert_dn: str | None = None
    disable_parallel_dml: bool = True
    tcp_connect_timeout: float | None = Field(default=None, gt=0)
    retry_count: int | None = Field(default=None, ge=0)
    retry_delay: int | None = Field(default=None, ge=0)
    https_proxy: str | None = None
    https_proxy_port: int | None = Field(default=None, ge=1, le=65535)
    table: str = "NATS_SINK_EVENTS"
    table_routes: list[OracleTableRoute] = Field(default_factory=list)
    mode: OracleWriteMode = "merge"
    merge_update_columns: list[str] | None = None
    auto_create: bool = False
    payload_mode: PayloadStorageMode = "json_or_envelope"
    payload_column: str | None = None
    headers_column: str | None = None
    columns: OracleColumnMapping = Field(default_factory=OracleColumnMapping)
    idempotency: OracleIdempotencyConfig = Field(default_factory=OracleIdempotencyConfig)
    staging: OracleStagingConfig = Field(default_factory=OracleStagingConfig)
    pool_min: int = Field(default=1, ge=1)
    pool_max: int = Field(default=4, ge=1)
    pool_increment: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_password_source(self) -> OracleSinkConfig:
        """Validate secret-source combinations and legacy column aliases."""

        if not self.password and not self.password_env:
            raise ValueError("either sink.password or sink.password_env must be configured")
        if self.wallet_password is not None and self.wallet_password_env is not None:
            raise ValueError(
                "configure either sink.wallet_password or sink.wallet_password_env, not both"
            )
        if self.wallet_password is not None and self.wallet_location is None:
            raise ValueError("sink.wallet_password requires sink.wallet_location")
        if self.wallet_password_env is not None and self.wallet_location is None:
            raise ValueError("sink.wallet_password_env requires sink.wallet_location")
        if self.https_proxy_port is not None and self.https_proxy is None:
            raise ValueError("sink.https_proxy_port requires sink.https_proxy")
        if self.payload_column:
            self.columns.payload = self.payload_column
        if self.headers_column:
            self.columns.headers = self.headers_column
        if self.merge_update_columns is not None and self.mode != "merge":
            raise ValueError("sink.merge_update_columns applies only when sink.mode is 'merge'")
        for route in self.table_routes:
            if route.merge_update_columns is not None and self.mode != "merge":
                raise ValueError(
                    "sink.table_routes[].merge_update_columns applies only when "
                    "sink.mode is 'merge'"
                )
        if self.staging.enabled and self.mode not in {"merge", "insert_ignore"}:
            raise ValueError(
                "sink.staging.enabled requires sink.mode to be 'merge' or 'insert_ignore'"
            )
        return self

    def resolve_password(self) -> str:
        """Resolve the Oracle password without exposing it in redacted output."""

        if self.password is not None:
            return self.password
        if not self.password_env:
            raise ConfigurationError("Oracle password_env is not configured")
        password = os.getenv(self.password_env)
        if password is None:
            raise ConfigurationError(f"environment variable {self.password_env} is not set")
        return password

    def resolve_wallet_password(self) -> str | None:
        """Resolve the Autonomous Database wallet password only at connection time."""

        if self.wallet_password is not None:
            return self.wallet_password
        if not self.wallet_password_env:
            return None
        password = os.getenv(self.wallet_password_env)
        if password is None:
            raise ConfigurationError(f"environment variable {self.wallet_password_env} is not set")
        return password
