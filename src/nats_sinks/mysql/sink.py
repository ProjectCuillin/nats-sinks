# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle MySQL sink implementation.

``MySqlSink`` writes normalized ``NatsEnvelope`` batches to Oracle MySQL with
the same core safety contract as the Oracle Database and file sinks: the method
returns success only after the destination write has crossed its durable
boundary.  For Oracle MySQL that boundary is an explicit transaction commit.

The sink owns only destination work.  It never receives raw NATS messages, never
ACKs JetStream messages, and never logs payloads, passwords, server addresses,
or certificate material.  Connection and table identifiers are validated before
SQL text is built; row values are always passed as driver bind parameters.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
    SerializationError,
    TemporarySinkError,
)
from nats_sinks.core.metrics import (
    MetricNames,
    MetricsRecorder,
    NoopMetrics,
    increment_metric,
    observe_metric,
)
from nats_sinks.core.payload import PayloadStorageMode
from nats_sinks.mysql.config import (
    MySqlIdempotencyConfig,
    MySqlSinkConfig,
    MySqlTableRoute,
    MySqlWriteMode,
)
from nats_sinks.mysql.ddl import create_events_table_ddl
from nats_sinks.mysql.errors import (
    ACCESS_DENIED_ERROR,
    CONNECTION_REFUSED_ERROR,
    DATA_TOO_LONG_ERROR,
    DEADLOCK_ERROR,
    DUPLICATE_KEY_ERROR,
    INVALID_JSON_TEXT_ERROR,
    LOCK_WAIT_TIMEOUT_ERROR,
    NO_SUCH_TABLE_ERROR,
    SERVER_GONE_AWAY_ERROR,
    SERVER_LOST_ERROR,
    SYNTAX_ERROR,
    UNKNOWN_COLUMN_ERROR,
    UNKNOWN_DATABASE_ERROR,
    is_duplicate_error,
    mysql_error_code,
)
from nats_sinks.mysql.mapping import envelope_to_row
from nats_sinks.mysql.routing import (
    resolve_route_for_subject,
    resolve_table_for_subject,
    validate_subject_pattern,
)
from nats_sinks.mysql.sql import MySqlWriteSql, build_write_sql, validate_identifier

LOGGER = logging.getLogger(__name__)

_SCHEMA_MISMATCH_HINT = (
    "The configured Oracle MySQL table may be missing columns expected by nats-sinks, "
    "or the configured column mapping may not match the table shape. Verify the "
    "target table, configured column names, idempotency key columns, and current "
    "recommended Oracle MySQL DDL. If this is a retained test table from an older "
    "release, migrate it or recreate it with the current schema."
)


@dataclass(frozen=True, slots=True)
class _MySqlWriteStats:
    """Best-effort Oracle MySQL write observations.

    These counters are operational signals only.  ACK behavior still depends
    exclusively on whether ``write_batch`` returns or raises after commit.
    """

    duplicates: int = 0
    duplicate_ignored: int = 0
    duplicate_noop: int = 0
    upsert_rows: int = 0
    upsert_outcome_unknown: int = 0


@dataclass(frozen=True, slots=True)
class _MySqlTablePolicy:
    """Effective Oracle MySQL write policy for one validated table."""

    table_name: str
    idempotency: MySqlIdempotencyConfig
    upsert_update_columns: list[str] | None


class MySqlSink:
    """Write NATS envelopes to Oracle MySQL and commit before returning success."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 3306,
        database: str,
        user: str,
        password: str | None = None,
        password_env: str | None = None,
        connection_timeout: float = 10.0,
        ssl_ca: str | None = None,
        ssl_cert: str | None = None,
        ssl_key: str | None = None,
        ssl_verify_identity: bool = True,
        ssl_disabled: bool = False,
        table: str = "NATS_SINK_EVENTS",
        mode: MySqlWriteMode = "upsert",
        upsert_update_columns: list[str] | None = None,
        auto_create: bool = False,
        payload_mode: PayloadStorageMode = "json_or_envelope",
        idempotency: MySqlIdempotencyConfig | dict[str, Any] | None = None,
        table_routes: list[MySqlTableRoute] | list[dict[str, Any]] | None = None,
        pool_name: str | None = None,
        pool_size: int = 4,
        config: MySqlSinkConfig | None = None,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        if config is None:
            try:
                config = MySqlSinkConfig.model_validate(
                    {
                        "type": "mysql",
                        "host": host,
                        "port": port,
                        "database": database,
                        "user": user,
                        "password": password,
                        "password_env": password_env,
                        "connection_timeout": connection_timeout,
                        "ssl_ca": ssl_ca,
                        "ssl_cert": ssl_cert,
                        "ssl_key": ssl_key,
                        "ssl_verify_identity": ssl_verify_identity,
                        "ssl_disabled": ssl_disabled,
                        "table": table,
                        "mode": mode,
                        "upsert_update_columns": upsert_update_columns,
                        "auto_create": auto_create,
                        "payload_mode": payload_mode,
                        "idempotency": idempotency or {},
                        "table_routes": table_routes or [],
                        "pool_name": pool_name,
                        "pool_size": pool_size,
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self.metrics: MetricsRecorder = metrics or NoopMetrics()
        self._pool: Any | None = None
        self._connector: Any | None = None
        self._pooling: Any | None = None
        self._write_sql_cache: dict[str, MySqlWriteSql] = {}
        self._table_policies: dict[str, _MySqlTablePolicy] = {}
        self._register_table_policy(
            table=self.config.table,
            idempotency=self.config.idempotency,
            upsert_update_columns=self.config.upsert_update_columns,
        )
        for route in self.config.table_routes:
            validate_subject_pattern(route.subject)
            self._register_table_policy(
                table=route.table,
                idempotency=route.idempotency or self.config.idempotency,
                upsert_update_columns=(
                    route.upsert_update_columns
                    if route.upsert_update_columns is not None
                    else self.config.upsert_update_columns
                ),
            )
        for policy in self._table_policies.values():
            self._prepare_sql_for_policy(policy)

        if self.config.mode == "append":
            LOGGER.warning(
                "Oracle MySQL append mode is not idempotent by default; use upsert or insert_ignore"
            )

    @classmethod
    def from_mapping(
        cls,
        raw_config: dict[str, Any],
        *,
        metrics: MetricsRecorder | None = None,
    ) -> MySqlSink:
        """Build an Oracle MySQL sink from a raw sink configuration mapping."""

        try:
            config = MySqlSinkConfig.model_validate(raw_config)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(
            host=config.host,
            port=config.port,
            database=config.database,
            user=config.user,
            password=config.password,
            password_env=config.password_env,
            connection_timeout=config.connection_timeout,
            ssl_ca=config.ssl_ca,
            ssl_cert=config.ssl_cert,
            ssl_key=config.ssl_key,
            ssl_verify_identity=config.ssl_verify_identity,
            ssl_disabled=config.ssl_disabled,
            table=config.table,
            mode=config.mode,
            upsert_update_columns=config.upsert_update_columns,
            auto_create=config.auto_create,
            payload_mode=config.payload_mode,
            idempotency=config.idempotency,
            table_routes=config.table_routes,
            pool_name=config.pool_name,
            pool_size=config.pool_size,
            config=config,
            metrics=metrics,
        )

    def set_metrics(self, metrics: MetricsRecorder | None) -> None:
        """Attach the runner-owned metrics recorder to this sink."""

        self.metrics = metrics or NoopMetrics()

    async def start(self) -> None:
        """Create the Oracle MySQL connection pool."""

        if self._pool is not None:
            return
        try:
            self._connector = importlib.import_module("mysql.connector")
            self._pooling = importlib.import_module("mysql.connector.pooling")
        except ImportError as exc:
            raise ConfigurationError("install nats-sinks[mysql] to use MySqlSink") from exc

        try:
            self._pool = await asyncio.to_thread(
                self._pooling.MySQLConnectionPool,
                **self._pool_options(),
            )
        except Exception as exc:
            raise self._translate_exception(
                exc,
                "failed to create Oracle MySQL connection pool",
            ) from exc

        if self.config.auto_create:
            await self.ensure_schema()

    async def stop(self) -> None:
        """Release the Oracle MySQL pool reference.

        Oracle MySQL Connector/Python pools do not expose a process-wide close
        method.  Individual connections are returned to the pool after every
        operation, and dropping the pool reference is enough for shutdown.
        """

        self._pool = None

    async def healthcheck(self) -> None:
        """Verify Oracle MySQL connectivity."""

        if self._pool is None:
            raise ConfigurationError("MySqlSink has not been started")
        try:
            await asyncio.to_thread(self._healthcheck_sync)
        except Exception as exc:
            raise self._translate_exception(exc, "Oracle MySQL healthcheck failed") from exc

    async def ensure_schema(self) -> None:
        """Create recommended Oracle MySQL tables only when explicitly enabled."""

        if self._pool is None:
            raise ConfigurationError("MySqlSink has not been started")
        tables = [self.config.table, *(route.table for route in self.config.table_routes)]
        for table in dict.fromkeys(tables):
            ddl = create_events_table_ddl(table)
            try:
                await asyncio.to_thread(self._execute_ddl_sync, ddl)
            except Exception as exc:
                raise self._translate_exception(
                    exc,
                    "Oracle MySQL schema creation failed",
                ) from exc

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write and commit a batch. Success means Oracle MySQL commit completed."""

        if not messages:
            return
        if self._pool is None:
            raise ConfigurationError("MySqlSink has not been started")

        try:
            rows_by_table = self._rows_by_table(messages)
            stats = await asyncio.to_thread(self._write_rows_sync, rows_by_table)
            self._record_write_stats(stats)
        except SerializationError:
            raise
        except TemporarySinkError:
            raise
        except PermanentSinkError:
            raise
        except Exception as exc:
            if is_duplicate_error(exc):
                self._record_mysql_conflict(len(messages))
            if self.config.mode == "insert_ignore" and is_duplicate_error(exc):
                self._record_duplicate_ignored(len(messages))
                return
            raise self._translate_exception(exc, "Oracle MySQL batch write failed") from exc

    def _pool_options(self) -> dict[str, Any]:
        """Build connection-pool options without logging resolved secrets."""

        options: dict[str, Any] = {
            "pool_size": self.config.pool_size,
            "host": self.config.host,
            "port": self.config.port,
            "database": self.config.database,
            "user": self.config.user,
            "password": self.config.resolve_password(),
            "connection_timeout": self._driver_connection_timeout_seconds(),
            "autocommit": False,
        }
        if self.config.pool_name:
            options["pool_name"] = self.config.pool_name
        if self.config.ssl_disabled:
            options["ssl_disabled"] = True
        else:
            if self.config.ssl_ca:
                options["ssl_ca"] = self.config.ssl_ca
                options["ssl_verify_identity"] = self.config.ssl_verify_identity
            if self.config.ssl_cert:
                options["ssl_cert"] = self.config.ssl_cert
            if self.config.ssl_key:
                options["ssl_key"] = self.config.ssl_key
        return options

    def _driver_connection_timeout_seconds(self) -> int:
        """Return the integer timeout shape expected by Oracle MySQL Connector/Python.

        The public configuration model accepts positive seconds as a number so
        operators can use familiar JSON values such as ``2.5``.  Connector/Python
        9.7.0 passes this value into a native connection object that expects an
        integer.  Rounding up preserves the operator's minimum wait intent
        without allowing a sub-second timeout to become zero.
        """

        return max(1, math.ceil(self.config.connection_timeout))

    def _healthcheck_sync(self) -> None:
        connection = self._connection()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("select 1")
                cursor.fetchone()
            finally:
                self._close_cursor(cursor)
        finally:
            self._close_connection(connection)

    def _execute_ddl_sync(self, ddl: str) -> None:
        connection = self._connection()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(ddl)
            finally:
                self._close_cursor(cursor)
            connection.commit()
        except Exception:
            self._rollback_connection_sync(connection)
            raise
        finally:
            self._close_connection(connection)

    def _rows_by_table(self, messages: Sequence[NatsEnvelope]) -> dict[str, list[dict[str, Any]]]:
        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        for message in messages:
            table = resolve_table_for_subject(
                message.subject,
                default_table=self.config.table,
                routes=self.config.table_routes,
            )
            row = envelope_to_row(
                message,
                idempotency=self._policy_for_subject(message.subject).idempotency,
                payload_mode=self.config.payload_mode,
            )
            rows_by_table.setdefault(table, []).append(row)
        return rows_by_table

    def _write_sql_for_policy(self, policy: _MySqlTablePolicy) -> MySqlWriteSql:
        sql = build_write_sql(
            table=policy.table_name,
            columns=self.config.columns,
            mode=self.config.mode,
            key_columns=policy.idempotency.columns,
            upsert_update_columns=policy.upsert_update_columns,
        )
        self._write_sql_cache[sql.table_name] = sql
        return sql

    def _prepare_sql_for_policy(self, policy: _MySqlTablePolicy) -> None:
        self._write_sql_for_policy(policy)

    def _register_table_policy(
        self,
        *,
        table: str,
        idempotency: MySqlIdempotencyConfig,
        upsert_update_columns: list[str] | None,
    ) -> None:
        """Register the effective policy for one Oracle MySQL table."""

        table_name = validate_identifier(table)
        policy = _MySqlTablePolicy(
            table_name=table_name,
            idempotency=idempotency,
            upsert_update_columns=upsert_update_columns,
        )
        existing = self._table_policies.get(table_name)
        if existing is not None and self._policy_signature(existing) != self._policy_signature(
            policy
        ):
            raise ConfigurationError(
                f"conflicting Oracle MySQL idempotency policy configured for table {table_name}"
            )
        self._table_policies[table_name] = policy

    def _policy_for_subject(self, subject: str) -> _MySqlTablePolicy:
        route = resolve_route_for_subject(subject, routes=self.config.table_routes)
        table = route.table if route is not None else self.config.table
        return self._policy_for_table_name(validate_identifier(table))

    def _policy_for_table_name(self, table_name: str) -> _MySqlTablePolicy:
        policy = self._table_policies.get(table_name)
        if policy is None:
            raise ConfigurationError(
                f"Oracle MySQL policy for table {table_name} is not configured"
            )
        return policy

    def _policy_signature(
        self,
        policy: _MySqlTablePolicy,
    ) -> tuple[str, tuple[str, ...], str | None, tuple[str, ...] | None]:
        update_columns: tuple[str, ...] | None = None
        if policy.upsert_update_columns is not None:
            update_columns = tuple(
                validate_identifier(column) for column in policy.upsert_update_columns
            )
        return (
            policy.idempotency.strategy,
            tuple(validate_identifier(column) for column in policy.idempotency.columns),
            policy.idempotency.payload_field,
            update_columns,
        )

    def _write_rows_sync(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> _MySqlWriteStats:
        connection = self._connection()
        stats = _MySqlWriteStats()
        commit_started: float | None = None
        try:
            cursor = connection.cursor()
            try:
                stats = self._write_direct_rows_sync(cursor, rows_by_table)
            finally:
                self._close_cursor(cursor)
            commit_started = time.perf_counter()
            connection.commit()
        except Exception as exc:
            if commit_started is not None:
                observe_metric(
                    self.metrics,
                    MetricNames.MYSQL_COMMIT_SECONDS,
                    time.perf_counter() - commit_started,
                )
                self._rollback_connection_sync(connection)
                raise DestinationUnavailableError("Oracle MySQL commit failed") from exc
            self._rollback_connection_sync(connection)
            raise
        else:
            observe_metric(
                self.metrics,
                MetricNames.MYSQL_COMMIT_SECONDS,
                time.perf_counter() - commit_started,
            )
        finally:
            self._close_connection(connection)
        return stats

    def _write_direct_rows_sync(
        self,
        cursor: Any,
        rows_by_table: dict[str, list[dict[str, Any]]],
    ) -> _MySqlWriteStats:
        stats = _MySqlWriteStats()
        for table, rows in rows_by_table.items():
            if not rows:
                continue
            sql = self._write_sql_cache.get(table)
            if sql is None:
                sql = self._write_sql_for_policy(self._policy_for_table_name(table))
            bind_rows = [self._bind_tuple(sql, row) for row in rows]
            execute_started = time.perf_counter()
            cursor.executemany(sql.sql, bind_rows)
            observe_metric(
                self.metrics,
                MetricNames.MYSQL_EXECUTE_SECONDS,
                time.perf_counter() - execute_started,
            )
            table_stats = self._write_stats_from_cursor(cursor, sql, rows)
            stats = self._combine_write_stats(stats, table_stats)
        return stats

    @staticmethod
    def _bind_tuple(sql: MySqlWriteSql, row: dict[str, Any]) -> tuple[Any, ...]:
        """Convert a row dictionary to the SQL builder's positional bind order."""

        return tuple(row[name] for name in sql.bind_names)

    def _write_stats_from_cursor(
        self,
        cursor: Any,
        sql: MySqlWriteSql,
        rows: Sequence[dict[str, Any]],
    ) -> _MySqlWriteStats:
        """Build committed-write metrics from Oracle MySQL execution metadata."""

        attempted = len(rows)
        if self.config.mode == "insert_ignore":
            duplicates = self._rowcount_no_change_count(cursor, rows)
            return _MySqlWriteStats(
                duplicates=duplicates,
                duplicate_ignored=duplicates,
            )
        if self.config.mode == "upsert":
            if not sql.update_columns:
                duplicates = self._rowcount_no_change_count(cursor, rows)
                return _MySqlWriteStats(
                    duplicates=duplicates,
                    duplicate_noop=duplicates,
                    upsert_rows=attempted,
                )
            return _MySqlWriteStats(
                upsert_rows=attempted,
                upsert_outcome_unknown=attempted,
            )
        return _MySqlWriteStats()

    def _combine_write_stats(
        self,
        left: _MySqlWriteStats,
        right: _MySqlWriteStats,
    ) -> _MySqlWriteStats:
        """Combine per-table observations without carrying raw row data."""

        return _MySqlWriteStats(
            duplicates=left.duplicates + right.duplicates,
            duplicate_ignored=left.duplicate_ignored + right.duplicate_ignored,
            duplicate_noop=left.duplicate_noop + right.duplicate_noop,
            upsert_rows=left.upsert_rows + right.upsert_rows,
            upsert_outcome_unknown=left.upsert_outcome_unknown + right.upsert_outcome_unknown,
        )

    def _rowcount_no_change_count(self, cursor: Any, rows: Sequence[dict[str, Any]]) -> int:
        """Estimate rows left unchanged when the driver exposes rowcount."""

        raw_rowcount = getattr(cursor, "rowcount", None)
        if raw_rowcount is None:
            return 0
        try:
            rowcount = int(raw_rowcount)
        except (TypeError, ValueError):
            return 0
        if rowcount < 0:
            return 0
        return max(len(rows) - min(rowcount, len(rows)), 0)

    def _record_write_stats(self, stats: object) -> None:
        """Record optional Oracle MySQL counters after committed success."""

        if not isinstance(stats, _MySqlWriteStats):
            return
        if stats.upsert_rows:
            increment_metric(self.metrics, MetricNames.MYSQL_UPSERT_ROWS_TOTAL, stats.upsert_rows)
        if stats.upsert_outcome_unknown:
            increment_metric(
                self.metrics,
                MetricNames.MYSQL_UPSERT_OUTCOME_UNKNOWN_TOTAL,
                stats.upsert_outcome_unknown,
            )
        if stats.duplicate_ignored:
            self._record_duplicate_ignored(stats.duplicate_ignored)
        if stats.duplicate_noop:
            self._record_duplicate_noop(stats.duplicate_noop)

    def _record_mysql_conflict(self, count: int) -> None:
        """Record Oracle MySQL conflicts without table names or payload data."""

        increment_metric(self.metrics, MetricNames.MYSQL_CONFLICTS_TOTAL, count)

    def _record_duplicate_ignored(self, count: int) -> None:
        """Record duplicate rows that were safe to treat as prior success."""

        if count <= 0:
            return
        increment_metric(self.metrics, MetricNames.MYSQL_DUPLICATES_TOTAL, count)
        increment_metric(self.metrics, MetricNames.MYSQL_DUPLICATE_IGNORED_TOTAL, count)

    def _record_duplicate_noop(self, count: int) -> None:
        """Record duplicate rows left unchanged by no-op upsert mode."""

        if count <= 0:
            return
        increment_metric(self.metrics, MetricNames.MYSQL_DUPLICATES_TOTAL, count)
        increment_metric(self.metrics, MetricNames.MYSQL_DUPLICATE_NOOP_TOTAL, count)

    def _connection(self) -> Any:
        pool = self._require_pool()
        return pool.get_connection()

    def _require_pool(self) -> Any:
        if self._pool is None:
            raise ConfigurationError("MySqlSink has not been started")
        return self._pool

    def _rollback_connection_sync(self, connection: Any) -> None:
        """Rollback a failed Oracle MySQL transaction when available."""

        rollback = getattr(connection, "rollback", None)
        if rollback is None:
            return
        try:
            rollback()
        except Exception:  # pragma: no cover - rollback failures are logged only.
            LOGGER.warning("Oracle MySQL rollback failed after write error", exc_info=True)

    @staticmethod
    def _close_cursor(cursor: Any) -> None:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _close_connection(connection: Any) -> None:
        close = getattr(connection, "close", None)
        if callable(close):
            close()

    def _translate_exception(
        self, exc: BaseException, context: str
    ) -> TemporarySinkError | PermanentSinkError:
        code = mysql_error_code(exc)
        permanent_message = self._permanent_error_message(code, context)
        if permanent_message is not None:
            return PermanentSinkError(permanent_message)
        if code == DUPLICATE_KEY_ERROR:
            return PermanentSinkError(f"{context}: duplicate key")
        if code in {
            CONNECTION_REFUSED_ERROR,
            SERVER_GONE_AWAY_ERROR,
            SERVER_LOST_ERROR,
            DEADLOCK_ERROR,
            LOCK_WAIT_TIMEOUT_ERROR,
        }:
            return DestinationUnavailableError(f"{context}: Oracle MySQL error {code}")
        fallback = code or type(exc).__name__
        return DestinationUnavailableError(f"{context}: Oracle MySQL error {fallback}")

    @staticmethod
    def _permanent_error_message(code: int | None, context: str) -> str | None:
        """Return a human-readable permanent Oracle MySQL error message."""

        messages = {
            ACCESS_DENIED_ERROR: (
                f"{context}: Oracle MySQL authentication failed. Verify the runtime user, "
                "password environment variable, TLS settings, and database name. "
                "Resolved secrets are intentionally not logged."
            ),
            UNKNOWN_DATABASE_ERROR: (
                f"{context}: Oracle MySQL database is not available to the runtime user. "
                "Verify the database name and grants."
            ),
            NO_SUCH_TABLE_ERROR: (
                f"{context}: Oracle MySQL table is not available to the runtime user. "
                "The configured table may not exist, may be in a different database, or "
                "the runtime account may be missing required table privileges."
            ),
            UNKNOWN_COLUMN_ERROR: (
                f"{context}: Oracle MySQL reported an unknown column. {_SCHEMA_MISMATCH_HINT}"
            ),
            SYNTAX_ERROR: (
                f"{context}: Oracle MySQL reported a SQL syntax error. {_SCHEMA_MISMATCH_HINT}"
            ),
        }
        if code in messages:
            return messages[code]
        if code in {DATA_TOO_LONG_ERROR, INVALID_JSON_TEXT_ERROR}:
            return (
                f"{context}: Oracle MySQL rejected row content for the configured schema. "
                f"{_SCHEMA_MISMATCH_HINT}"
            )
        return None
