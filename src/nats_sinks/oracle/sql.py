# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
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
from nats_sinks.oracle.config import OracleColumnMapping, OracleStagingCleanupMode, OracleWriteMode

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")

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
}


@dataclass(frozen=True, slots=True)
class OracleWriteSql:
    """Generated SQL and key metadata."""

    table_name: str
    sql: str
    bind_names: tuple[str, ...]
    columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    update_columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OracleStagingSql:
    """Generated SQL for the optional staging-table merge path."""

    target_table_name: str
    staging_table_name: str
    batch_id_column: str
    batch_bind_name: str
    insert_sql: str
    merge_sql: str
    cleanup_sql: str | None
    bind_names: tuple[str, ...]
    columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    update_columns: tuple[str, ...] = ()


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


def _merge_update_columns(
    *,
    column_names: tuple[str, ...],
    key_columns: tuple[str, ...],
    configured_update_columns: list[str] | None,
) -> tuple[str, ...]:
    """Return validated Oracle columns that may be updated by `merge`.

    `None` preserves the original nats-sinks behavior and updates every
    non-key column.  An explicit empty list means "do not update matched rows";
    in that mode the merge inserts missing rows and leaves duplicates
    unchanged.  Explicit column names are validated against the generated
    column mapping because Oracle identifiers cannot be protected by bind
    variables.
    """

    if configured_update_columns is None:
        return tuple(column for column in column_names if column not in key_columns)

    requested = tuple(validate_identifier(column) for column in configured_update_columns)
    if len(requested) != len(set(requested)):
        raise ConfigurationError("merge_update_columns must not contain duplicate columns")

    allowed_columns = set(column_names)
    unknown_columns = [column for column in requested if column not in allowed_columns]
    if unknown_columns:
        raise ConfigurationError(
            "merge_update_columns contains columns that are not present in the Oracle column "
            f"mapping: {', '.join(unknown_columns)}"
        )

    key_column_set = set(key_columns)
    key_overlap = [column for column in requested if column in key_column_set]
    if key_overlap:
        raise ConfigurationError(
            "merge_update_columns must not include idempotency key columns: "
            f"{', '.join(key_overlap)}"
        )

    return requested


def build_write_sql(
    *,
    table: str,
    columns: OracleColumnMapping,
    mode: OracleWriteMode,
    key_columns: list[str],
    merge_update_columns: list[str] | None = None,
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
            update_columns=(),
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
            update_columns=(),
        )

    update_columns = _merge_update_columns(
        column_names=column_names,
        key_columns=validated_key_columns,
        configured_update_columns=merge_update_columns,
    )
    matched_clause = ""
    if update_columns:
        set_clause = ", ".join(f"target.{column} = source.{column}" for column in update_columns)
        matched_clause = f"when matched then update set {set_clause} "  # nosec B608
    # Identifiers are allow-list validated above; all data values remain bind variables.
    sql = (
        f"merge into {table_name} target "  # noqa: S608  # nosec B608
        f"using (select {selects} from dual) source "
        f"on ({on_clause}) "
        f"{matched_clause}"
        f"when not matched then insert ({', '.join(column_names)}) values ({insert_values})"
    )
    return OracleWriteSql(
        table_name=table_name,
        sql=sql,
        bind_names=bind_names,
        columns=column_names,
        key_columns=validated_key_columns,
        update_columns=update_columns,
    )


def build_staging_merge_sql(
    *,
    target_table: str,
    staging_table: str,
    batch_id_column: str,
    columns: OracleColumnMapping,
    mode: OracleWriteMode,
    key_columns: list[str],
    merge_update_columns: list[str] | None = None,
    cleanup: OracleStagingCleanupMode = "delete_on_success",
) -> OracleStagingSql:
    """Generate SQL for array-loading a staging table and set-merging target rows.

    The generated statements keep all row values as bind variables.  Only
    table and column identifiers are interpolated, and every identifier is
    validated through the same strict allow-list as the normal write path.
    """

    if mode not in {"merge", "insert_ignore"}:
        raise ConfigurationError("staging merge SQL requires mode 'merge' or 'insert_ignore'")

    target_table_name = validate_identifier(target_table)
    staging_table_name = validate_identifier(staging_table)
    validated_batch_id_column = validate_identifier(batch_id_column)
    write_columns = _write_columns(columns)
    bind_names = tuple(bind for bind, _column in write_columns)
    column_names = tuple(column for _bind, column in write_columns)
    validated_key_columns = tuple(validate_identifier(column) for column in key_columns)
    if not validated_key_columns:
        raise ConfigurationError("staging merge requires at least one idempotency key column")

    batch_bind_name = "nats_sinks_batch_id"
    staging_columns = (validated_batch_id_column, *column_names)
    staging_binds = (batch_bind_name, *bind_names)
    insert_values = ", ".join(f":{bind}" for bind in staging_binds)
    # Identifiers are allow-list validated above; all data values remain bind variables.
    insert_sql = (
        f"insert into {staging_table_name} ({', '.join(staging_columns)}) values ({insert_values})"  # noqa: S608  # nosec B608
    )

    source_columns = ", ".join(column_names)
    on_clause = " and ".join(
        f"target.{column} = source.{column}" for column in validated_key_columns
    )
    merge_insert_values = ", ".join(f"source.{column}" for column in column_names)
    merge_prefix = (
        f"merge into {target_table_name} target "  # noqa: S608  # nosec B608
        f"using (select {source_columns} from {staging_table_name} "
        f"where {validated_batch_id_column} = :{batch_bind_name}) source "
        f"on ({on_clause}) "
    )
    if mode == "insert_ignore":
        merge_sql = (
            f"{merge_prefix}when not matched then insert ({', '.join(column_names)}) "  # noqa: S608  # nosec B608
            f"values ({merge_insert_values})"
        )
        update_columns: tuple[str, ...] = ()
    else:
        update_columns = _merge_update_columns(
            column_names=column_names,
            key_columns=validated_key_columns,
            configured_update_columns=merge_update_columns,
        )
        matched_clause = ""
        if update_columns:
            set_clause = ", ".join(
                f"target.{column} = source.{column}" for column in update_columns
            )
            matched_clause = f"when matched then update set {set_clause} "  # nosec B608
        merge_sql = (
            f"{merge_prefix}{matched_clause}"  # noqa: S608  # nosec B608
            f"when not matched then insert ({', '.join(column_names)}) "
            f"values ({merge_insert_values})"
        )

    cleanup_sql = None
    if cleanup == "delete_on_success":
        # Identifiers are allow-list validated above; all data values remain bind variables.
        cleanup_sql = (
            f"delete from {staging_table_name} "  # noqa: S608  # nosec B608
            f"where {validated_batch_id_column} = :{batch_bind_name}"
        )

    return OracleStagingSql(
        target_table_name=target_table_name,
        staging_table_name=staging_table_name,
        batch_id_column=validated_batch_id_column,
        batch_bind_name=batch_bind_name,
        insert_sql=insert_sql,
        merge_sql=merge_sql,
        cleanup_sql=cleanup_sql,
        bind_names=(batch_bind_name, *bind_names),
        columns=column_names,
        key_columns=validated_key_columns,
        update_columns=update_columns,
    )
