# SPDX-License-Identifier: Apache-2.0
"""Oracle SQL generation with strict identifier validation.

Oracle bind variables can protect values, but they cannot bind table or column
names.  This module therefore validates every configured identifier with a
strict allow-list before constructing SQL text.  Values remain bind variables
in all generated statements.

SQL generation is separated from connection handling so security-sensitive
identifier behavior can be tested without Oracle.  Bandit is told to ignore the
validated SQL construction lines with targeted `nosec` comments; those comments
must not be broadened without preserving the validation tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.oracle.config import OracleColumnMapping, OracleWriteMode

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")

FIELD_TO_BIND = {
    "stream_name": "stream_name",
    "stream_sequence": "stream_sequence",
    "subject": "subject",
    "message_id": "message_id",
    "message_created_at_epoch_ns": "message_created_at_epoch_ns",
    "jetstream_timestamp_epoch_ns": "jetstream_timestamp_epoch_ns",
    "received_at_epoch_ns": "received_at_epoch_ns",
    "stored_at_epoch_ns": "stored_at_epoch_ns",
    "payload": "payload_json",
    "headers": "headers_json",
    "metadata": "metadata_json",
}


@dataclass(frozen=True, slots=True)
class OracleWriteSql:
    """Generated SQL and key metadata."""

    table_name: str
    sql: str
    bind_names: tuple[str, ...]
    columns: tuple[str, ...]
    key_columns: tuple[str, ...]


def validate_identifier(identifier: str) -> str:
    """Validate and normalize an Oracle identifier or dotted schema.table name."""

    if not isinstance(identifier, str):
        raise ConfigurationError(f"invalid Oracle identifier {identifier!r}")
    parts = identifier.split(".")
    if not parts or any(not part for part in parts):
        raise ConfigurationError(f"invalid Oracle identifier {identifier!r}")
    for part in parts:
        if not _IDENTIFIER_RE.fullmatch(part):
            raise ConfigurationError(f"invalid Oracle identifier {identifier!r}")
    return ".".join(part.upper() for part in parts)


def _validated_columns(columns: OracleColumnMapping) -> dict[str, str]:
    values = columns.model_dump()
    return {field: validate_identifier(column) for field, column in values.items()}


def _write_columns(columns: OracleColumnMapping) -> tuple[tuple[str, str], ...]:
    validated = _validated_columns(columns)
    return tuple((FIELD_TO_BIND[field], column) for field, column in validated.items())


def build_write_sql(
    *,
    table: str,
    columns: OracleColumnMapping,
    mode: OracleWriteMode,
    key_columns: list[str],
) -> OracleWriteSql:
    """Generate bind-variable SQL for a configured Oracle write mode."""

    table_name = validate_identifier(table)
    write_columns = _write_columns(columns)
    bind_names = tuple(bind for bind, _column in write_columns)
    column_names = tuple(column for _bind, column in write_columns)
    validated_key_columns = tuple(validate_identifier(column) for column in key_columns)

    if not validated_key_columns and mode in {"merge", "insert_ignore"}:
        raise ConfigurationError(f"mode {mode!r} requires at least one idempotency key column")

    if mode in {"insert", "append"}:
        hint = " /*+ append */" if mode == "append" else ""
        binds = ", ".join(f":{bind}" for bind in bind_names)
        # Identifiers are allow-list validated above; all data values remain bind variables.
        sql = f"insert{hint} into {table_name} ({', '.join(column_names)}) values ({binds})"  # nosec B608
        return OracleWriteSql(
            table_name=table_name,
            sql=sql,
            bind_names=bind_names,
            columns=column_names,
            key_columns=(),
        )

    selects = ", ".join(f":{bind} as {column}" for bind, column in write_columns)
    on_clause = " and ".join(
        f"target.{column} = source.{column}" for column in validated_key_columns
    )
    insert_values = ", ".join(f"source.{column}" for column in column_names)

    if mode == "insert_ignore":
        # Identifiers are allow-list validated above; all data values remain bind variables.
        sql = (
            f"merge into {table_name} target "  # noqa: S608  # nosec B608
            f"using (select {selects} from dual) source "
            f"on ({on_clause}) "
            f"when not matched then insert ({', '.join(column_names)}) values ({insert_values})"
        )
        return OracleWriteSql(
            table_name=table_name,
            sql=sql,
            bind_names=bind_names,
            columns=column_names,
            key_columns=validated_key_columns,
        )

    update_columns = [column for column in column_names if column not in validated_key_columns]
    set_clause = ", ".join(f"target.{column} = source.{column}" for column in update_columns)
    # Identifiers are allow-list validated above; all data values remain bind variables.
    sql = (
        f"merge into {table_name} target "  # noqa: S608  # nosec B608
        f"using (select {selects} from dual) source "
        f"on ({on_clause}) "
        f"when matched then update set {set_clause} "
        f"when not matched then insert ({', '.join(column_names)}) values ({insert_values})"
    )
    return OracleWriteSql(
        table_name=table_name,
        sql=sql,
        bind_names=bind_names,
        columns=column_names,
        key_columns=validated_key_columns,
    )
