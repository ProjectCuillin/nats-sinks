# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle MySQL SQL generation with strict identifier validation.

Bind parameters protect values, but database APIs cannot bind table or column
names.  This module therefore validates every configured identifier with a
small allow-list before SQL text is assembled.  Row values remain positional
``%s`` placeholders for Oracle MySQL Connector/Python.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.mysql.config import MySqlColumnMapping, MySqlWriteMode

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,63}$")

FIELD_TO_BIND = {
    "stream_name": "stream_name",
    "stream_sequence": "stream_sequence",
    "subject": "subject",
    "message_id": "message_id",
    "priority": "priority",
    "classification": "classification",
    "labels": "labels",
    "message_created_at_epoch_ns": "message_created_at_epoch_ns",
    "jetstream_timestamp_epoch_ns": "jetstream_timestamp_epoch_ns",
    "received_at_epoch_ns": "received_at_epoch_ns",
    "stored_at_epoch_ns": "stored_at_epoch_ns",
    "payload": "payload_json",
    "headers": "headers_json",
    "metadata": "metadata_json",
    "mission_metadata": "mission_metadata_json",
    "security_labels": "security_labels_json",
}


@dataclass(frozen=True, slots=True)
class MySqlWriteSql:
    """Generated SQL and positional-bind metadata."""

    table_name: str
    quoted_table_name: str
    sql: str
    bind_names: tuple[str, ...]
    columns: tuple[str, ...]
    quoted_columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    update_columns: tuple[str, ...] = ()


def validate_identifier(identifier: str) -> str:
    """Validate an Oracle MySQL identifier or dotted schema.table name."""

    if not isinstance(identifier, str):
        raise ConfigurationError(f"invalid Oracle MySQL identifier {identifier!r}")
    parts = identifier.split(".")
    if not parts or any(not part for part in parts):
        raise ConfigurationError(f"invalid Oracle MySQL identifier {identifier!r}")
    for part in parts:
        if not _IDENTIFIER_RE.fullmatch(part):
            raise ConfigurationError(f"invalid Oracle MySQL identifier {identifier!r}")
    return ".".join(parts)


def quote_identifier(identifier: str) -> str:
    """Return a backtick-quoted identifier after allow-list validation."""

    validated = validate_identifier(identifier)
    return ".".join(f"`{part}`" for part in validated.split("."))


def _validated_columns(columns: MySqlColumnMapping) -> dict[str, str]:
    values = columns.model_dump()
    return {field: validate_identifier(column) for field, column in values.items()}


def _write_columns(columns: MySqlColumnMapping) -> tuple[tuple[str, str], ...]:
    validated = _validated_columns(columns)
    return tuple((FIELD_TO_BIND[field], column) for field, column in validated.items())


def _upsert_update_columns(
    *,
    column_names: tuple[str, ...],
    key_columns: tuple[str, ...],
    configured_update_columns: list[str] | None,
) -> tuple[str, ...]:
    """Return validated non-key columns that Oracle MySQL may update."""

    if configured_update_columns is None:
        return tuple(column for column in column_names if column not in key_columns)

    requested = tuple(validate_identifier(column) for column in configured_update_columns)
    if len(requested) != len(set(requested)):
        raise ConfigurationError("upsert_update_columns must not contain duplicate columns")

    allowed_columns = set(column_names)
    unknown_columns = [column for column in requested if column not in allowed_columns]
    if unknown_columns:
        raise ConfigurationError(
            "upsert_update_columns contains columns that are not present in the "
            f"Oracle MySQL column mapping: {', '.join(unknown_columns)}"
        )

    key_column_set = set(key_columns)
    key_overlap = [column for column in requested if column in key_column_set]
    if key_overlap:
        raise ConfigurationError(
            "upsert_update_columns must not include idempotency key columns: "
            f"{', '.join(key_overlap)}"
        )

    return requested


def build_write_sql(
    *,
    table: str,
    columns: MySqlColumnMapping,
    mode: MySqlWriteMode,
    key_columns: list[str],
    upsert_update_columns: list[str] | None = None,
) -> MySqlWriteSql:
    """Generate positional-placeholder SQL for one Oracle MySQL write mode."""

    table_name = validate_identifier(table)
    quoted_table_name = quote_identifier(table_name)
    write_columns = _write_columns(columns)
    bind_names = tuple(bind for bind, _column in write_columns)
    column_names = tuple(column for _bind, column in write_columns)
    quoted_columns = tuple(quote_identifier(column) for column in column_names)
    validated_key_columns = tuple(validate_identifier(column) for column in key_columns)

    if not validated_key_columns and mode in {"upsert", "insert_ignore"}:
        raise ConfigurationError(f"mode {mode!r} requires at least one idempotency key column")

    placeholders = ", ".join("%s" for _ in bind_names)
    column_list = ", ".join(quoted_columns)

    if mode in {"insert", "append"}:
        # Identifiers are allow-list validated above; all data values remain placeholders.
        sql = f"insert into {quoted_table_name} ({column_list}) values ({placeholders})"  # noqa: S608  # nosec B608
        return MySqlWriteSql(
            table_name=table_name,
            quoted_table_name=quoted_table_name,
            sql=sql,
            bind_names=bind_names,
            columns=column_names,
            quoted_columns=quoted_columns,
            key_columns=(),
        )

    if mode == "insert_ignore":
        # Identifiers are allow-list validated above; all data values remain placeholders.
        sql = f"insert ignore into {quoted_table_name} ({column_list}) values ({placeholders})"  # noqa: S608  # nosec B608
        return MySqlWriteSql(
            table_name=table_name,
            quoted_table_name=quoted_table_name,
            sql=sql,
            bind_names=bind_names,
            columns=column_names,
            quoted_columns=quoted_columns,
            key_columns=validated_key_columns,
        )

    update_columns = _upsert_update_columns(
        column_names=column_names,
        key_columns=validated_key_columns,
        configured_update_columns=upsert_update_columns,
    )
    if update_columns:
        assignments = ", ".join(
            f"{quote_identifier(column)} = values({quote_identifier(column)})"
            for column in update_columns
        )
    else:
        # Oracle MySQL requires an update expression for ON DUPLICATE KEY
        # UPDATE.  A key-column self-assignment makes duplicate redelivery a
        # no-op while still returning success after commit.
        first_key = quote_identifier(validated_key_columns[0])
        assignments = f"{first_key} = {first_key}"
    # Identifiers are allow-list validated above; all data values remain placeholders.
    sql = (
        f"insert into {quoted_table_name} ({column_list}) values ({placeholders}) "  # noqa: S608  # nosec B608
        f"on duplicate key update {assignments}"
    )
    return MySqlWriteSql(
        table_name=table_name,
        quoted_table_name=quoted_table_name,
        sql=sql,
        bind_names=bind_names,
        columns=column_names,
        quoted_columns=quoted_columns,
        key_columns=validated_key_columns,
        update_columns=update_columns,
    )
