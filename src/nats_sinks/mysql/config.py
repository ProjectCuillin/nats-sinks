# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration models for the first-party Oracle MySQL sink.

The Oracle MySQL sink validates destination configuration before a connection
pool is created.  The models are intentionally explicit rather than accepting
arbitrary driver keyword arguments: host, TLS, table routing, idempotency, and
pool settings are the trust boundary between user-supplied JSON configuration
and the database driver.

Production deployments should prefer ``password_env`` and local TLS trust
material instead of inline secrets.  The sink resolves secrets only at
connection time and never includes resolved passwords in redacted
configuration output, exceptions, metrics, or logs.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.payload import PayloadStorageMode

MySqlWriteMode = Literal["insert", "insert_ignore", "upsert", "append"]
IdempotencyStrategy = Literal["stream_sequence", "message_id", "payload_field"]


class MySqlIdempotencyConfig(BaseModel):
    """Oracle MySQL idempotency configuration.

    ``stream_sequence`` is the recommended default because JetStream stream
    name and sequence form a stable delivery identity.  ``message_id`` and
    ``payload_field`` are available when publishers already provide a stronger
    business id, but they require upstream discipline.
    """

    model_config = ConfigDict(extra="forbid")

    strategy: IdempotencyStrategy = "stream_sequence"
    columns: list[str] = Field(default_factory=lambda: ["STREAM_NAME", "STREAM_SEQUENCE"])
    payload_field: str | None = None

    @field_validator("payload_field")
    @classmethod
    def validate_payload_field(cls, value: str | None) -> str | None:
        """Validate dotted payload-field paths before any message is processed."""

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
    def validate_payload_strategy(self) -> MySqlIdempotencyConfig:
        """Validate strategy-specific fields and choose sensible key columns."""

        if self.strategy == "payload_field" and not self.payload_field:
            raise ValueError("idempotency.payload_field is required for payload_field strategy")
        if self.strategy == "message_id" and self.columns == ["STREAM_NAME", "STREAM_SEQUENCE"]:
            self.columns = ["MESSAGE_ID"]
        if self.strategy == "payload_field" and self.columns == ["STREAM_NAME", "STREAM_SEQUENCE"]:
            self.columns = ["MESSAGE_ID"]
        return self


class MySqlColumnMapping(BaseModel):
    """Oracle MySQL table column mapping.

    The defaults mirror the Oracle Database sink so operators can reason about
    both relational sinks with one schema vocabulary.  Identifier values are
    validated by the SQL builder before SQL text is produced.
    """

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


class MySqlTableRoute(BaseModel):
    """Route a NATS subject pattern to one Oracle MySQL table and policy."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    table: str
    idempotency: MySqlIdempotencyConfig | None = None
    upsert_update_columns: list[str] | None = None


class MySqlSinkConfig(BaseModel):
    """Validated configuration for ``MySqlSink``.

    The sink accepts TCP connection settings used by Oracle MySQL
    Connector/Python.  TLS verification is enabled whenever TLS material is
    provided and hostname verification remains enabled by default.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["mysql"] = "mysql"
    host: str = "127.0.0.1"
    port: int = Field(default=3306, ge=1, le=65535)
    database: str
    user: str
    password: str | None = None
    password_env: str | None = None
    connection_timeout: float = Field(default=10.0, gt=0)
    ssl_ca: str | None = None
    ssl_cert: str | None = None
    ssl_key: str | None = None
    ssl_verify_identity: bool = True
    ssl_disabled: bool = False
    table: str = "NATS_SINK_EVENTS"
    table_routes: list[MySqlTableRoute] = Field(default_factory=list)
    mode: MySqlWriteMode = "upsert"
    upsert_update_columns: list[str] | None = None
    auto_create: bool = False
    payload_mode: PayloadStorageMode = "json_or_envelope"
    payload_column: str | None = None
    headers_column: str | None = None
    columns: MySqlColumnMapping = Field(default_factory=MySqlColumnMapping)
    idempotency: MySqlIdempotencyConfig = Field(default_factory=MySqlIdempotencyConfig)
    pool_name: str | None = None
    pool_size: int = Field(default=4, ge=1, le=32)

    @field_validator("host", "database", "user")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        """Reject empty connection fields before a driver call is made."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("Oracle MySQL connection fields must not be empty")
        return rendered

    @model_validator(mode="after")
    def validate_secret_and_mode(self) -> MySqlSinkConfig:
        """Validate secret sources, TLS options, and mode-specific fields."""

        if not self.password and not self.password_env:
            raise ValueError("either sink.password or sink.password_env must be configured")
        if self.ssl_disabled and any((self.ssl_ca, self.ssl_cert, self.ssl_key)):
            raise ValueError("sink.ssl_disabled cannot be combined with TLS certificate options")
        if self.ssl_cert and not self.ssl_key:
            raise ValueError("sink.ssl_cert requires sink.ssl_key")
        if self.ssl_key and not self.ssl_cert:
            raise ValueError("sink.ssl_key requires sink.ssl_cert")
        if self.payload_column:
            self.columns.payload = self.payload_column
        if self.headers_column:
            self.columns.headers = self.headers_column
        if self.upsert_update_columns is not None and self.mode != "upsert":
            raise ValueError("sink.upsert_update_columns applies only when sink.mode is 'upsert'")
        for route in self.table_routes:
            if route.upsert_update_columns is not None and self.mode != "upsert":
                raise ValueError(
                    "sink.table_routes[].upsert_update_columns applies only when "
                    "sink.mode is 'upsert'"
                )
        return self

    def resolve_password(self) -> str:
        """Resolve the Oracle MySQL password from a direct value or environment."""

        if self.password is not None:
            return self.password
        if not self.password_env:
            raise ConfigurationError("Oracle MySQL password_env is not configured")
        password = os.getenv(self.password_env)
        if password is None:
            raise ConfigurationError(f"environment variable {self.password_env} is not set")
        return password
