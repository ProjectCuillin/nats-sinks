# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Read-only Oracle lineage and correlation query helpers.

The helpers in this module are deliberately conservative.  They inspect rows
that `OracleSink` has already written, but they do not participate in delivery,
ACK, retry, DLQ, or idempotency decisions.  Their job is to help an operator
answer questions such as "which persisted events share this mission_id?" while
keeping query construction easy to review.

Values are always supplied as bind variables.  Table names, column names, and
JSON paths cannot be bound by Oracle, so they are accepted only from validated
configuration and a small allow-list of supported lineage fields.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from nats_sinks.core.errors import ConfigurationError, DestinationUnavailableError
from nats_sinks.oracle.config import OracleColumnMapping, OracleSinkConfig
from nats_sinks.oracle.connection import build_oracle_pool_options
from nats_sinks.oracle.errors import oracle_error_code
from nats_sinks.oracle.sql import validate_identifier

LineageField = Literal[
    "correlation_id",
    "causation_id",
    "mission_id",
    "tasking_id",
    "track_id",
    "message_id",
    "subject",
]

DEFAULT_LINEAGE_LIMIT = 50
MAX_LINEAGE_LIMIT = 1_000
MAX_LINEAGE_VALUE_LENGTH = 512
ASCII_CONTROL_MAX = 31
ASCII_DELETE = 127


@dataclass(frozen=True, slots=True)
class LineageFieldSpec:
    """Allow-listed field that may be used for lineage lookup."""

    field: LineageField
    description: str
    column_attribute: str | None = None
    mission_metadata_json_path: str | None = None

    def where_expression(self, columns: OracleColumnMapping) -> str:
        """Return a safe Oracle SQL expression for the configured column map."""

        if self.column_attribute is not None:
            column_name = validate_identifier(getattr(columns, self.column_attribute))
            return column_name
        if self.mission_metadata_json_path is not None:
            column_name = validate_identifier(columns.mission_metadata)
            return f"json_value({column_name}, '{self.mission_metadata_json_path}')"
        raise ConfigurationError(f"lineage field {self.field!r} is not queryable")


LINEAGE_FIELD_SPECS: dict[LineageField, LineageFieldSpec] = {
    "correlation_id": LineageFieldSpec(
        field="correlation_id",
        description="Application or mission correlation identifier in mission metadata.",
        mission_metadata_json_path="$.correlation_id",
    ),
    "causation_id": LineageFieldSpec(
        field="causation_id",
        description="Application or mission causation identifier in mission metadata.",
        mission_metadata_json_path="$.causation_id",
    ),
    "mission_id": LineageFieldSpec(
        field="mission_id",
        description="Mission identifier in mission metadata.",
        mission_metadata_json_path="$.mission_id",
    ),
    "tasking_id": LineageFieldSpec(
        field="tasking_id",
        description="Tasking identifier in mission metadata.",
        mission_metadata_json_path="$.tasking_id",
    ),
    "track_id": LineageFieldSpec(
        field="track_id",
        description="Track identifier in mission metadata.",
        mission_metadata_json_path="$.track_id",
    ),
    "message_id": LineageFieldSpec(
        field="message_id",
        description="NATS message identifier stored in the configured message-id column.",
        column_attribute="message_id",
    ),
    "subject": LineageFieldSpec(
        field="subject",
        description="NATS subject stored in the configured subject column.",
        column_attribute="subject",
    ),
}


@dataclass(frozen=True, slots=True)
class OracleLineageQuery:
    """Generated safe Oracle query and bind values."""

    sql: str
    binds: Mapping[str, str]
    table_name: str
    field: LineageField
    limit: int
    aliases: tuple[str, ...]
    include_payload: bool = False


@dataclass(frozen=True, slots=True)
class OracleLineageRecord:
    """One redacted lineage record returned by Oracle."""

    stream_name: str | None
    stream_sequence: int | None
    subject: str | None
    message_id: str | None
    priority: str | None
    classification: str | None
    labels: str | None
    message_created_at_epoch_ns: int | None
    received_at_epoch_ns: int | None
    stored_at_epoch_ns: int | None
    mission_metadata_keys: tuple[str, ...]
    payload_json: Any | None = None

    def to_dict(self, *, include_payload: bool = False) -> dict[str, Any]:
        """Render a script-friendly dictionary without raw payload by default."""

        rendered: dict[str, Any] = {
            "stream_name": self.stream_name,
            "stream_sequence": self.stream_sequence,
            "subject": self.subject,
            "message_id": self.message_id,
            "priority": self.priority,
            "classification": self.classification,
            "labels": self.labels,
            "message_created_at_epoch_ns": self.message_created_at_epoch_ns,
            "received_at_epoch_ns": self.received_at_epoch_ns,
            "stored_at_epoch_ns": self.stored_at_epoch_ns,
            "mission_metadata_keys": list(self.mission_metadata_keys),
            "payload_included": include_payload,
        }
        if include_payload:
            rendered["payload_json"] = self.payload_json
        return rendered


@dataclass(frozen=True, slots=True)
class OracleLineageResult:
    """Bounded lineage query result."""

    field: LineageField
    table_name: str
    limit: int
    records: tuple[OracleLineageRecord, ...]
    include_payload: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Render result as JSON-safe data for CLI output."""

        return {
            "field": self.field,
            "table": self.table_name,
            "limit": self.limit,
            "record_count": len(self.records),
            "payload_included": self.include_payload,
            "records": [
                record.to_dict(include_payload=self.include_payload) for record in self.records
            ],
        }


def normalize_lineage_field(field: str) -> LineageField:
    """Normalize and allow-list a lineage query field name."""

    normalized = field.strip().casefold().replace("-", "_")
    if normalized not in LINEAGE_FIELD_SPECS:
        allowed = ", ".join(sorted(LINEAGE_FIELD_SPECS))
        raise ConfigurationError(f"lineage field must be one of: {allowed}")
    return cast(LineageField, normalized)


def validate_lineage_value(value: str) -> str:
    """Validate a user-supplied lineage identifier value."""

    rendered = value.strip()
    if not rendered:
        raise ConfigurationError("lineage value must not be empty")
    if len(rendered) > MAX_LINEAGE_VALUE_LENGTH:
        raise ConfigurationError(
            f"lineage value must be {MAX_LINEAGE_VALUE_LENGTH} characters or fewer"
        )
    if any(
        ord(character) <= ASCII_CONTROL_MAX or ord(character) == ASCII_DELETE
        for character in rendered
    ):
        raise ConfigurationError("lineage value must not contain control characters")
    return rendered


def validate_lineage_limit(limit: int) -> int:
    """Validate the bounded result limit used in Oracle query text."""

    if limit < 1 or limit > MAX_LINEAGE_LIMIT:
        raise ConfigurationError(f"lineage limit must be between 1 and {MAX_LINEAGE_LIMIT}")
    return limit


def configured_oracle_lineage_tables(config: OracleSinkConfig) -> tuple[str, ...]:
    """Return the configured table allow-list for lineage queries."""

    tables = [config.table, *(route.table for route in config.table_routes)]
    return tuple(dict.fromkeys(validate_identifier(table) for table in tables))


def resolve_lineage_table(config: OracleSinkConfig, requested_table: str | None) -> str:
    """Resolve a requested table against the configured Oracle sink allow-list."""

    allowed = configured_oracle_lineage_tables(config)
    if requested_table is None:
        return allowed[0]
    normalized = validate_identifier(requested_table)
    if normalized not in allowed:
        raise ConfigurationError(
            "lineage table must be the configured sink.table or one of sink.table_routes[].table"
        )
    return normalized


def build_oracle_lineage_query(
    *,
    table: str,
    columns: OracleColumnMapping,
    field: str,
    value: str,
    limit: int = DEFAULT_LINEAGE_LIMIT,
    include_payload: bool = False,
) -> OracleLineageQuery:
    """Build a parameterized Oracle lineage query for one allow-listed field."""

    normalized_field = normalize_lineage_field(field)
    cleaned_value = validate_lineage_value(value)
    bounded_limit = validate_lineage_limit(limit)
    table_name = validate_identifier(table)
    spec = LINEAGE_FIELD_SPECS[normalized_field]
    where_expression = spec.where_expression(columns)

    select_items: list[tuple[str, str]] = [
        ("stream_name", validate_identifier(columns.stream_name)),
        ("stream_sequence", validate_identifier(columns.stream_sequence)),
        ("subject", validate_identifier(columns.subject)),
        ("message_id", validate_identifier(columns.message_id)),
        ("priority", validate_identifier(columns.priority)),
        ("classification", validate_identifier(columns.classification)),
        ("labels", validate_identifier(columns.labels)),
        ("message_created_at_epoch_ns", validate_identifier(columns.message_created_at_epoch_ns)),
        ("received_at_epoch_ns", validate_identifier(columns.received_at_epoch_ns)),
        ("stored_at_epoch_ns", validate_identifier(columns.stored_at_epoch_ns)),
        ("mission_metadata_json", validate_identifier(columns.mission_metadata)),
    ]
    if include_payload:
        select_items.append(("payload_json", validate_identifier(columns.payload)))

    aliases = tuple(alias for alias, _expression in select_items)
    select_sql = ", ".join(f"{expression} as {alias}" for alias, expression in select_items)
    received_column = validate_identifier(columns.received_at_epoch_ns)
    sequence_column = validate_identifier(columns.stream_sequence)
    sql = (
        f"select {select_sql} from {table_name} "  # noqa: S608  # nosec B608
        f"where {where_expression} = :lineage_value "
        f"order by {received_column} nulls last, {sequence_column} nulls last "
        f"fetch first {bounded_limit} rows only"
    )
    return OracleLineageQuery(
        sql=sql,
        binds={"lineage_value": cleaned_value},
        table_name=table_name,
        field=normalized_field,
        limit=bounded_limit,
        aliases=aliases,
        include_payload=include_payload,
    )


def _read_lob_or_value(value: Any) -> Any:
    """Return a database value, reading LOB-like objects when necessary."""

    read = getattr(value, "read", None)
    if callable(read):
        return read()
    return value


def _json_keys(value: Any) -> tuple[str, ...]:
    """Return JSON object keys without exposing full metadata values."""

    raw_value = _read_lob_or_value(value)
    if raw_value is None:
        return ()
    if isinstance(raw_value, Mapping):
        return tuple(sorted(str(key) for key in raw_value))
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return ()
        if isinstance(parsed, Mapping):
            return tuple(sorted(str(key) for key in parsed))
    return ()


def _json_value(value: Any) -> Any:
    """Parse an optional JSON value for explicitly requested payload output."""

    raw_value = _read_lob_or_value(value)
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return {"_nats_sinks_unparsed_payload": True}
    return raw_value


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def oracle_lineage_records_from_rows(
    rows: Iterable[Sequence[Any]],
    *,
    aliases: Sequence[str],
    include_payload: bool = False,
) -> tuple[OracleLineageRecord, ...]:
    """Convert Oracle cursor rows to redacted lineage records."""

    records: list[OracleLineageRecord] = []
    for row in rows:
        values = dict(zip(aliases, row, strict=True))
        records.append(
            OracleLineageRecord(
                stream_name=values.get("stream_name"),
                stream_sequence=_int_or_none(values.get("stream_sequence")),
                subject=values.get("subject"),
                message_id=values.get("message_id"),
                priority=values.get("priority"),
                classification=values.get("classification"),
                labels=values.get("labels"),
                message_created_at_epoch_ns=_int_or_none(values.get("message_created_at_epoch_ns")),
                received_at_epoch_ns=_int_or_none(values.get("received_at_epoch_ns")),
                stored_at_epoch_ns=_int_or_none(values.get("stored_at_epoch_ns")),
                mission_metadata_keys=_json_keys(values.get("mission_metadata_json")),
                payload_json=_json_value(values.get("payload_json")) if include_payload else None,
            )
        )
    return tuple(records)


def render_lineage_result_text(result: OracleLineageResult) -> str:
    """Render lineage result as concise text for humans and shell logs."""

    lines = [
        "Lineage query result",
        f"field={result.field}",
        f"table={result.table_name}",
        f"limit={result.limit}",
        f"records={len(result.records)}",
    ]
    for index, record in enumerate(result.records, start=1):
        metadata_keys = ",".join(record.mission_metadata_keys) or "-"
        lines.append(
            f"{index}. stream={record.stream_name or '-'} "
            f"sequence={record.stream_sequence if record.stream_sequence is not None else '-'} "
            f"subject={record.subject or '-'} "
            f"message_id={record.message_id or '-'} "
            f"priority={record.priority or '-'} "
            f"classification={record.classification or '-'} "
            f"labels={record.labels or '-'} "
            f"received_at_epoch_ns="
            f"{record.received_at_epoch_ns if record.received_at_epoch_ns is not None else '-'} "
            f"mission_metadata_keys={metadata_keys} "
            f"payload={'included' if result.include_payload else 'omitted'}"
        )
    return "\n".join(lines)


class OracleLineageReader:
    """Small read-only Oracle query runner for persisted lineage records."""

    def __init__(self, config: OracleSinkConfig) -> None:
        self.config = config

    async def query(
        self,
        *,
        field: str,
        value: str,
        table: str | None = None,
        limit: int = DEFAULT_LINEAGE_LIMIT,
        include_payload: bool = False,
    ) -> OracleLineageResult:
        """Run a bounded read-only lineage query in a worker thread."""

        return await asyncio.to_thread(
            self.query_sync,
            field=field,
            value=value,
            table=table,
            limit=limit,
            include_payload=include_payload,
        )

    def query_sync(
        self,
        *,
        field: str,
        value: str,
        table: str | None = None,
        limit: int = DEFAULT_LINEAGE_LIMIT,
        include_payload: bool = False,
    ) -> OracleLineageResult:
        """Run a bounded read-only lineage query using python-oracledb."""

        table_name = resolve_lineage_table(self.config, table)
        query = build_oracle_lineage_query(
            table=table_name,
            columns=self.config.columns,
            field=field,
            value=value,
            limit=limit,
            include_payload=include_payload,
        )
        try:
            oracledb = importlib.import_module("oracledb")
        except ImportError as exc:
            raise ConfigurationError("install nats-sinks[oracle] to query Oracle lineage") from exc

        pool = None
        try:
            pool = oracledb.create_pool(**build_oracle_pool_options(self.config))
            with pool.acquire() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query.sql, dict(query.binds))
                    rows = cursor.fetchall()
        except Exception as exc:
            code = oracle_error_code(exc)
            raise DestinationUnavailableError(
                f"Oracle lineage query failed: {code or type(exc).__name__}"
            ) from exc
        finally:
            if pool is not None:
                close = getattr(pool, "close", None)
                if callable(close):
                    try:
                        close()
                    except TypeError:
                        close(force=True)

        records = oracle_lineage_records_from_rows(
            rows,
            aliases=query.aliases,
            include_payload=include_payload,
        )
        return OracleLineageResult(
            field=query.field,
            table_name=query.table_name,
            limit=query.limit,
            records=records,
            include_payload=include_payload,
        )
