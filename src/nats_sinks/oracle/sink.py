# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle sink implementation.

`OracleSink` is the first production destination sink for nats-sinks.  It owns
Oracle connection-pool lifecycle, optional schema creation, batch row mapping,
SQL execution, transaction commit, health checks, and translation of Oracle
driver failures into framework errors.  The connection layer supports standard
Oracle Net listeners as well as Oracle Autonomous Database walletless TLS and
wallet/mTLS options exposed by python-oracledb.

The sink never receives raw NATS messages and never ACKs JetStream messages.
For OracleSink, a batch is successful only after the Oracle transaction has
committed.  If commit fails, the sink raises a temporary framework error and
the core runner leaves the source messages eligible for redelivery.

Production deployments should prefer `merge` or `insert_ignore` mode with a
stable idempotency key.  `append` mode exists for specialized workloads but is
not idempotent by default.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
import uuid
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
from nats_sinks.oracle.config import (
    OracleIdempotencyConfig,
    OracleSinkConfig,
    OracleStagingConfig,
    OracleTableRoute,
    OracleWriteMode,
)
from nats_sinks.oracle.connection import build_oracle_pool_options
from nats_sinks.oracle.ddl import create_events_table_ddl, create_staging_events_table_ddl
from nats_sinks.oracle.errors import is_duplicate_error, oracle_error_code
from nats_sinks.oracle.mapping import envelope_to_row
from nats_sinks.oracle.routing import (
    resolve_route_for_subject,
    resolve_table_for_subject,
    validate_subject_pattern,
)
from nats_sinks.oracle.sql import (
    OracleStagingSql,
    OracleWriteSql,
    build_staging_merge_sql,
    build_write_sql,
    validate_identifier,
)

LOGGER = logging.getLogger(__name__)

_SCHEMA_MISMATCH_HINT = (
    "The configured Oracle table may be missing columns expected by nats-sinks, "
    "or the configured column mapping may not match the table shape. Verify the "
    "target table, configured column names, idempotency key columns, and current "
    "recommended Oracle DDL. If this is a retained test table from an older "
    "release, migrate it or recreate it with the current schema."
)


@dataclass(frozen=True, slots=True)
class _OracleWriteStats:
    """Small internal summary of Oracle write observations.

    The core ACK contract still depends only on whether `write_batch` returns
    or raises. These counts are best-effort operational signals for idempotent
    Oracle modes and must never change commit or ACK behavior.
    """

    duplicates: int = 0
    duplicate_ignored: int = 0
    duplicate_noop: int = 0
    merge_rows: int = 0
    merge_outcome_unknown: int = 0


@dataclass(frozen=True, slots=True)
class _OracleTablePolicy:
    """Effective Oracle write policy for one validated target table."""

    table_name: str
    idempotency: OracleIdempotencyConfig
    merge_update_columns: list[str] | None


class OracleSink:
    """Write NATS envelopes to Oracle and commit before returning success."""

    def __init__(
        self,
        *,
        dsn: str,
        user: str,
        password: str | None = None,
        password_env: str | None = None,
        config_dir: str | None = None,
        wallet_location: str | None = None,
        wallet_password: str | None = None,
        wallet_password_env: str | None = None,
        ssl_server_dn_match: bool | None = None,
        ssl_server_cert_dn: str | None = None,
        disable_parallel_dml: bool = True,
        tcp_connect_timeout: float | None = None,
        retry_count: int | None = None,
        retry_delay: int | None = None,
        https_proxy: str | None = None,
        https_proxy_port: int | None = None,
        table: str = "NATS_SINK_EVENTS",
        mode: OracleWriteMode = "merge",
        merge_update_columns: list[str] | None = None,
        auto_create: bool = False,
        payload_mode: PayloadStorageMode = "json_or_envelope",
        idempotency: OracleIdempotencyConfig | dict[str, Any] | None = None,
        staging: OracleStagingConfig | dict[str, Any] | None = None,
        table_routes: list[OracleTableRoute] | list[dict[str, Any]] | None = None,
        config: OracleSinkConfig | None = None,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        if config is None:
            try:
                config = OracleSinkConfig.model_validate(
                    {
                        "type": "oracle",
                        "dsn": dsn,
                        "user": user,
                        "password": password,
                        "password_env": password_env,
                        "config_dir": config_dir,
                        "wallet_location": wallet_location,
                        "wallet_password": wallet_password,
                        "wallet_password_env": wallet_password_env,
                        "ssl_server_dn_match": ssl_server_dn_match,
                        "ssl_server_cert_dn": ssl_server_cert_dn,
                        "disable_parallel_dml": disable_parallel_dml,
                        "tcp_connect_timeout": tcp_connect_timeout,
                        "retry_count": retry_count,
                        "retry_delay": retry_delay,
                        "https_proxy": https_proxy,
                        "https_proxy_port": https_proxy_port,
                        "table": table,
                        "mode": mode,
                        "merge_update_columns": merge_update_columns,
                        "auto_create": auto_create,
                        "payload_mode": payload_mode,
                        "idempotency": idempotency or {},
                        "staging": staging or {},
                        "table_routes": table_routes or [],
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self.metrics: MetricsRecorder = metrics or NoopMetrics()
        self._pool: Any | None = None
        self._oracledb: Any | None = None
        self._write_sql_cache: dict[str, OracleWriteSql] = {}
        self._staging_sql_cache: dict[str, OracleStagingSql] = {}
        self._table_policies: dict[str, _OracleTablePolicy] = {}
        self._register_table_policy(
            table=self.config.table,
            idempotency=self.config.idempotency,
            merge_update_columns=self.config.merge_update_columns,
        )
        for route in self.config.table_routes:
            validate_subject_pattern(route.subject)
            self._register_table_policy(
                table=route.table,
                idempotency=route.idempotency or self.config.idempotency,
                merge_update_columns=(
                    route.merge_update_columns
                    if route.merge_update_columns is not None
                    else self.config.merge_update_columns
                ),
            )
        for policy in self._table_policies.values():
            self._prepare_sql_for_policy(policy)

        if self.config.mode == "append":
            LOGGER.warning(
                "Oracle append mode is not idempotent by default; use merge or insert_ignore"
            )

    @classmethod
    def from_mapping(
        cls,
        raw_config: dict[str, Any],
        *,
        metrics: MetricsRecorder | None = None,
    ) -> OracleSink:
        """Build an Oracle sink from a raw sink configuration mapping."""

        try:
            config = OracleSinkConfig.model_validate(raw_config)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(
            dsn=config.dsn,
            user=config.user,
            password=config.password,
            password_env=config.password_env,
            config_dir=config.config_dir,
            wallet_location=config.wallet_location,
            wallet_password=config.wallet_password,
            wallet_password_env=config.wallet_password_env,
            ssl_server_dn_match=config.ssl_server_dn_match,
            ssl_server_cert_dn=config.ssl_server_cert_dn,
            disable_parallel_dml=config.disable_parallel_dml,
            tcp_connect_timeout=config.tcp_connect_timeout,
            retry_count=config.retry_count,
            retry_delay=config.retry_delay,
            https_proxy=config.https_proxy,
            https_proxy_port=config.https_proxy_port,
            table=config.table,
            mode=config.mode,
            merge_update_columns=config.merge_update_columns,
            auto_create=config.auto_create,
            payload_mode=config.payload_mode,
            staging=config.staging,
            config=config,
            metrics=metrics,
        )

    def set_metrics(self, metrics: MetricsRecorder | None) -> None:
        """Attach the runner-owned metrics recorder to this sink.

        The CLI constructs sinks before it starts the runner.  This small hook
        lets Oracle-specific idempotency counters land in the same local JSON
        snapshot as the core delivery counters without giving the sink any
        control over ACK decisions.
        """

        self.metrics = metrics or NoopMetrics()

    async def start(self) -> None:
        """Create the Oracle connection pool."""

        if self._pool is not None:
            return
        try:
            self._oracledb = importlib.import_module("oracledb")
        except ImportError as exc:
            raise ConfigurationError("install nats-sinks[oracle] to use OracleSink") from exc

        try:
            self._pool = await asyncio.to_thread(self._oracledb.create_pool, **self._pool_options())
        except Exception as exc:
            raise self._translate_exception(exc, "failed to create Oracle connection pool") from exc

        if self.config.auto_create:
            await self.ensure_schema()

    async def stop(self) -> None:
        """Close the Oracle connection pool."""

        if self._pool is None:
            return
        pool = self._pool
        self._pool = None
        close = getattr(pool, "close", None)
        if close is None:
            return
        try:
            await asyncio.to_thread(close)
        except TypeError:
            await asyncio.to_thread(close, force=True)

    async def healthcheck(self) -> None:
        """Verify Oracle connectivity."""

        if self._pool is None:
            raise ConfigurationError("OracleSink has not been started")
        try:
            await asyncio.to_thread(self._healthcheck_sync)
        except Exception as exc:
            raise self._translate_exception(exc, "Oracle healthcheck failed") from exc

    async def ensure_schema(self) -> None:
        """Create recommended tables only when explicitly enabled."""

        if self._pool is None:
            raise ConfigurationError("OracleSink has not been started")
        tables = [self.config.table, *(route.table for route in self.config.table_routes)]
        for table in dict.fromkeys(tables):
            ddl = create_events_table_ddl(table)
            try:
                await asyncio.to_thread(self._execute_ddl_sync, ddl)
            except Exception as exc:
                if oracle_error_code(exc) == "ORA-00955":
                    continue
                raise self._translate_exception(exc, "Oracle schema creation failed") from exc
        if self.config.staging.enabled and self.config.staging.table:
            ddl = create_staging_events_table_ddl(
                self.config.staging.table,
                batch_id_column=self.config.staging.batch_id_column,
            )
            try:
                await asyncio.to_thread(self._execute_ddl_sync, ddl)
            except Exception as exc:
                if oracle_error_code(exc) == "ORA-00955":
                    return
                raise self._translate_exception(
                    exc, "Oracle staging schema creation failed"
                ) from exc

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write and commit a batch. Success means Oracle commit completed."""

        if not messages:
            return
        if self._pool is None:
            raise ConfigurationError("OracleSink has not been started")

        try:
            rows_by_table = self._rows_by_table(messages)
            stats = await asyncio.to_thread(self._write_rows_sync, rows_by_table)
            self._record_write_stats(stats)
        except SerializationError:
            raise
        except PermanentSinkError:
            raise
        except Exception as exc:
            if is_duplicate_error(exc):
                self._record_oracle_conflict(len(messages))
            if self.config.mode == "insert_ignore" and is_duplicate_error(exc):
                self._record_duplicate_ignored(len(messages))
                return
            raise self._translate_exception(exc, "Oracle batch write failed") from exc

    def _pool_options(self) -> dict[str, Any]:
        """Build `oracledb.create_pool` options without logging resolved secrets."""

        return build_oracle_pool_options(self.config)

    def _healthcheck_sync(self) -> None:
        pool = self._require_pool()
        with pool.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute("select 1 from dual")
                cursor.fetchone()

    def _execute_ddl_sync(self, ddl: str) -> None:
        pool = self._require_pool()
        with pool.acquire() as connection:
            self._prepare_connection_sync(connection)
            with connection.cursor() as cursor:
                cursor.execute(ddl)
            connection.commit()

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

    def _write_sql_for_policy(self, policy: _OracleTablePolicy) -> OracleWriteSql:
        sql = build_write_sql(
            table=policy.table_name,
            columns=self.config.columns,
            mode=self.config.mode,
            key_columns=policy.idempotency.columns,
            merge_update_columns=policy.merge_update_columns,
        )
        self._write_sql_cache[sql.table_name] = sql
        return sql

    def _staging_sql_for_policy(self, policy: _OracleTablePolicy) -> OracleStagingSql:
        if not self.config.staging.table:
            raise ConfigurationError("Oracle staging table is not configured")
        sql = build_staging_merge_sql(
            target_table=policy.table_name,
            staging_table=self.config.staging.table,
            batch_id_column=self.config.staging.batch_id_column,
            columns=self.config.columns,
            mode=self.config.mode,
            key_columns=policy.idempotency.columns,
            merge_update_columns=policy.merge_update_columns,
            cleanup=self.config.staging.cleanup,
        )
        self._staging_sql_cache[sql.target_table_name] = sql
        return sql

    def _prepare_sql_for_policy(self, policy: _OracleTablePolicy) -> None:
        self._write_sql_for_policy(policy)
        if self.config.staging.enabled:
            self._staging_sql_for_policy(policy)

    def _register_table_policy(
        self,
        *,
        table: str,
        idempotency: OracleIdempotencyConfig,
        merge_update_columns: list[str] | None,
    ) -> None:
        """Register the effective policy for one target table.

        Multiple subject routes may point to the same table, but they must not
        disagree about the idempotency key or merge update behavior.  Allowing
        conflicting policies for the same table would make duplicates
        unpredictable and would hide a configuration error until production
        redelivery.
        """

        table_name = validate_identifier(table)
        policy = _OracleTablePolicy(
            table_name=table_name,
            idempotency=idempotency,
            merge_update_columns=merge_update_columns,
        )
        existing = self._table_policies.get(table_name)
        if existing is not None and self._policy_signature(existing) != self._policy_signature(
            policy
        ):
            raise ConfigurationError(
                f"conflicting Oracle idempotency policy configured for table {table_name}"
            )
        self._table_policies[table_name] = policy

    def _policy_for_subject(self, subject: str) -> _OracleTablePolicy:
        route = resolve_route_for_subject(subject, routes=self.config.table_routes)
        table = route.table if route is not None else self.config.table
        return self._policy_for_table_name(validate_identifier(table))

    def _policy_for_table_name(self, table_name: str) -> _OracleTablePolicy:
        policy = self._table_policies.get(table_name)
        if policy is None:
            raise ConfigurationError(f"Oracle policy for table {table_name} is not configured")
        return policy

    def _policy_signature(
        self,
        policy: _OracleTablePolicy,
    ) -> tuple[str, tuple[str, ...], str | None, tuple[str, ...] | None]:
        update_columns: tuple[str, ...] | None = None
        if policy.merge_update_columns is not None:
            update_columns = tuple(
                validate_identifier(column) for column in policy.merge_update_columns
            )
        return (
            policy.idempotency.strategy,
            tuple(validate_identifier(column) for column in policy.idempotency.columns),
            policy.idempotency.payload_field,
            update_columns,
        )

    def _write_rows_sync(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> _OracleWriteStats:
        pool = self._require_pool()
        stats = _OracleWriteStats()
        with pool.acquire() as connection:
            commit_started: float | None = None
            try:
                self._prepare_connection_sync(connection)
                with connection.cursor() as cursor:
                    if self.config.staging.enabled:
                        stats = self._combine_write_stats(
                            stats, self._write_staging_rows_sync(cursor, rows_by_table)
                        )
                    else:
                        stats = self._combine_write_stats(
                            stats, self._write_direct_rows_sync(cursor, rows_by_table)
                        )
                commit_started = time.perf_counter()
                connection.commit()
            except Exception as exc:
                if commit_started is not None:
                    observe_metric(
                        self.metrics,
                        MetricNames.ORACLE_COMMIT_SECONDS,
                        time.perf_counter() - commit_started,
                    )
                    self._rollback_connection_sync(connection)
                    raise DestinationUnavailableError("Oracle commit failed") from exc
                self._rollback_connection_sync(connection)
                raise
            else:
                observe_metric(
                    self.metrics,
                    MetricNames.ORACLE_COMMIT_SECONDS,
                    time.perf_counter() - commit_started,
                )
        return stats

    def _write_direct_rows_sync(
        self,
        cursor: Any,
        rows_by_table: dict[str, list[dict[str, Any]]],
    ) -> _OracleWriteStats:
        stats = _OracleWriteStats()
        for table, rows in rows_by_table.items():
            if not rows:
                continue
            sql = self._write_sql_cache.get(table)
            if sql is None:
                sql = self._write_sql_for_policy(self._policy_for_table_name(table))
            execute_started = time.perf_counter()
            cursor.executemany(sql.sql, rows)
            observe_metric(
                self.metrics,
                MetricNames.ORACLE_EXECUTE_SECONDS,
                time.perf_counter() - execute_started,
            )
            stats = self._combine_write_stats(stats, self._write_stats_from_cursor(cursor, rows))
        return stats

    def _write_staging_rows_sync(
        self,
        cursor: Any,
        rows_by_table: dict[str, list[dict[str, Any]]],
    ) -> _OracleWriteStats:
        stats = _OracleWriteStats()
        for table, rows in rows_by_table.items():
            if not rows:
                continue
            sql = self._staging_sql_cache.get(table)
            if sql is None:
                sql = self._staging_sql_for_policy(self._policy_for_table_name(table))
            batch_id = uuid.uuid4().hex
            staging_rows = [{sql.batch_bind_name: batch_id, **row} for row in rows]
            execute_started = time.perf_counter()
            cursor.executemany(sql.insert_sql, staging_rows)
            cursor.execute(sql.merge_sql, {sql.batch_bind_name: batch_id})
            stats = self._combine_write_stats(stats, self._write_stats_from_cursor(cursor, rows))
            if sql.cleanup_sql is not None:
                cursor.execute(sql.cleanup_sql, {sql.batch_bind_name: batch_id})
            observe_metric(
                self.metrics,
                MetricNames.ORACLE_EXECUTE_SECONDS,
                time.perf_counter() - execute_started,
            )
        return stats

    def _write_stats_from_cursor(
        self,
        cursor: Any,
        rows: Sequence[dict[str, Any]],
    ) -> _OracleWriteStats:
        """Build committed-write metrics from stable Oracle execution metadata.

        Oracle exposes affected-row counts for some `merge` statements, but it
        does not reliably tell the Python client which individual rows were
        inserted versus updated.  nats-sinks therefore records only outcomes it
        can describe honestly.  In update-enabled merge mode the rows are
        counted as processed with unknown insert-versus-match outcome.  In
        insert-only idempotent paths, a lower rowcount means a prior durable row
        was safely left unchanged.
        """

        attempted = len(rows)
        if self.config.mode == "insert_ignore":
            duplicates = self._rowcount_no_change_count(cursor, rows)
            return _OracleWriteStats(
                duplicates=duplicates,
                duplicate_ignored=duplicates,
            )
        if self.config.mode == "merge":
            if self.config.merge_update_columns == []:
                duplicates = self._rowcount_no_change_count(cursor, rows)
                return _OracleWriteStats(
                    duplicates=duplicates,
                    duplicate_noop=duplicates,
                    merge_rows=attempted,
                )
            return _OracleWriteStats(
                merge_rows=attempted,
                merge_outcome_unknown=attempted,
            )
        return _OracleWriteStats()

    def _combine_write_stats(
        self,
        left: _OracleWriteStats,
        right: _OracleWriteStats,
    ) -> _OracleWriteStats:
        """Combine per-table write observations without carrying raw row data."""

        return _OracleWriteStats(
            duplicates=left.duplicates + right.duplicates,
            duplicate_ignored=left.duplicate_ignored + right.duplicate_ignored,
            duplicate_noop=left.duplicate_noop + right.duplicate_noop,
            merge_rows=left.merge_rows + right.merge_rows,
            merge_outcome_unknown=left.merge_outcome_unknown + right.merge_outcome_unknown,
        )

    def _duplicate_ignored_count(self, cursor: Any, rows: Sequence[dict[str, Any]]) -> int:
        """Estimate duplicates skipped by `insert_ignore` using Oracle rowcount.

        `insert_ignore` is generated as a `merge` with only a `when not matched`
        insert clause.  For duplicate idempotency keys Oracle performs no data
        change, so `cursor.rowcount` can be lower than the attempted row count.
        Some drivers or database versions may not expose a useful rowcount; in
        those cases the sink reports zero rather than guessing.
        """

        if self.config.mode != "insert_ignore":
            return 0
        return self._rowcount_no_change_count(cursor, rows)

    def _rowcount_no_change_count(self, cursor: Any, rows: Sequence[dict[str, Any]]) -> int:
        """Estimate rows left unchanged when Oracle exposes affected rowcount."""

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

    def _rollback_connection_sync(self, connection: Any) -> None:
        """Rollback a failed Oracle transaction when the driver exposes rollback."""

        rollback = getattr(connection, "rollback", None)
        if rollback is None:
            return
        try:
            rollback()
        except Exception:  # pragma: no cover - rollback failures are logged only.
            LOGGER.warning("Oracle rollback failed after write error", exc_info=True)

    def _record_write_stats(self, stats: object) -> None:
        """Record optional Oracle-specific counters after committed success."""

        if not isinstance(stats, _OracleWriteStats):
            return
        if stats.merge_rows:
            increment_metric(self.metrics, MetricNames.ORACLE_MERGE_ROWS_TOTAL, stats.merge_rows)
        if stats.merge_outcome_unknown:
            increment_metric(
                self.metrics,
                MetricNames.ORACLE_MERGE_OUTCOME_UNKNOWN_TOTAL,
                stats.merge_outcome_unknown,
            )
        if stats.duplicate_ignored:
            self._record_duplicate_ignored(stats.duplicate_ignored)
        if stats.duplicate_noop:
            self._record_duplicate_noop(stats.duplicate_noop)

    def _record_oracle_conflict(self, count: int) -> None:
        """Record Oracle conflicts without including table names or payload data."""

        increment_metric(self.metrics, MetricNames.ORACLE_CONFLICTS_TOTAL, count)

    def _record_duplicate_ignored(self, count: int) -> None:
        """Record duplicate rows that were safe to treat as prior success."""

        if count <= 0:
            return
        increment_metric(self.metrics, MetricNames.ORACLE_DUPLICATES_TOTAL, count)
        increment_metric(self.metrics, MetricNames.ORACLE_DUPLICATE_IGNORED_TOTAL, count)

    def _record_duplicate_noop(self, count: int) -> None:
        """Record duplicate rows left unchanged by merge with no update columns."""

        if count <= 0:
            return
        increment_metric(self.metrics, MetricNames.ORACLE_DUPLICATES_TOTAL, count)
        increment_metric(self.metrics, MetricNames.ORACLE_DUPLICATE_NOOP_TOTAL, count)

    def _prepare_connection_sync(self, connection: Any) -> None:
        """Apply session settings that keep sink writes transaction-friendly."""

        if not self.config.disable_parallel_dml:
            return
        with connection.cursor() as cursor:
            cursor.execute("alter session disable parallel dml")

    def _require_pool(self) -> Any:
        if self._pool is None:
            raise ConfigurationError("OracleSink has not been started")
        return self._pool

    def _translate_exception(
        self, exc: BaseException, context: str
    ) -> TemporarySinkError | PermanentSinkError:
        code = oracle_error_code(exc)
        if code == "ORA-01017":
            return PermanentSinkError(
                f"{context}: ORA-01017 authentication failed. Verify the Oracle user, "
                "password environment variable, wallet settings, and database service name. "
                "Resolved secrets are intentionally not logged."
            )
        if code == "ORA-00942":
            return PermanentSinkError(
                f"{context}: ORA-00942 table or view is not available to the runtime user. "
                "The configured table may not exist, may be in a different schema, or the "
                "runtime account may be missing required table privileges. Verify the table "
                "name, schema owner, grants, migrations, and auto_create setting."
            )
        if code == "ORA-00904":
            return PermanentSinkError(
                f"{context}: ORA-00904 invalid Oracle identifier. {_SCHEMA_MISMATCH_HINT}"
            )
        if code == "ORA-00001":
            return PermanentSinkError(f"{context}: duplicate key")
        return DestinationUnavailableError(f"{context}: {code or type(exc).__name__}")
