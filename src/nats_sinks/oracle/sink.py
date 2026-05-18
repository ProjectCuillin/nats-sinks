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
from collections.abc import Sequence
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
from nats_sinks.core.payload import PayloadStorageMode
from nats_sinks.oracle.config import (
    OracleIdempotencyConfig,
    OracleSinkConfig,
    OracleTableRoute,
    OracleWriteMode,
)
from nats_sinks.oracle.ddl import create_events_table_ddl
from nats_sinks.oracle.errors import is_duplicate_error, oracle_error_code
from nats_sinks.oracle.mapping import envelope_to_row
from nats_sinks.oracle.routing import resolve_table_for_subject, validate_subject_pattern
from nats_sinks.oracle.sql import OracleWriteSql, build_write_sql

LOGGER = logging.getLogger(__name__)


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
        auto_create: bool = False,
        payload_mode: PayloadStorageMode = "json_or_envelope",
        idempotency: OracleIdempotencyConfig | dict[str, Any] | None = None,
        table_routes: list[OracleTableRoute] | list[dict[str, Any]] | None = None,
        config: OracleSinkConfig | None = None,
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
                        "auto_create": auto_create,
                        "payload_mode": payload_mode,
                        "idempotency": idempotency or {},
                        "table_routes": table_routes or [],
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self._pool: Any | None = None
        self._oracledb: Any | None = None
        self._write_sql_cache: dict[str, OracleWriteSql] = {}
        self._write_sql_for_table(self.config.table)
        for route in self.config.table_routes:
            validate_subject_pattern(route.subject)
            self._write_sql_for_table(route.table)

        if self.config.mode == "append":
            LOGGER.warning(
                "Oracle append mode is not idempotent by default; use merge or insert_ignore"
            )

    @classmethod
    def from_mapping(cls, raw_config: dict[str, Any]) -> OracleSink:
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
            auto_create=config.auto_create,
            payload_mode=config.payload_mode,
            config=config,
        )

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

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write and commit a batch. Success means Oracle commit completed."""

        if not messages:
            return
        if self._pool is None:
            raise ConfigurationError("OracleSink has not been started")

        try:
            rows_by_table = self._rows_by_table(messages)
            await asyncio.to_thread(self._write_rows_sync, rows_by_table)
        except SerializationError:
            raise
        except PermanentSinkError:
            raise
        except Exception as exc:
            if self.config.mode == "insert_ignore" and is_duplicate_error(exc):
                return
            raise self._translate_exception(exc, "Oracle batch write failed") from exc

    def _pool_options(self) -> dict[str, Any]:
        """Build `oracledb.create_pool` options without logging resolved secrets."""

        optional_options = {
            "config_dir": self.config.config_dir,
            "wallet_location": self.config.wallet_location,
            "wallet_password": self.config.resolve_wallet_password(),
            "ssl_server_dn_match": self.config.ssl_server_dn_match,
            "ssl_server_cert_dn": self.config.ssl_server_cert_dn,
            "tcp_connect_timeout": self.config.tcp_connect_timeout,
            "retry_count": self.config.retry_count,
            "retry_delay": self.config.retry_delay,
            "https_proxy": self.config.https_proxy,
            "https_proxy_port": self.config.https_proxy_port,
        }
        return {
            key: value
            for key, value in {
                "user": self.config.user,
                "password": self.config.resolve_password(),
                "dsn": self.config.dsn,
                "min": self.config.pool_min,
                "max": self.config.pool_max,
                "increment": self.config.pool_increment,
                **optional_options,
            }.items()
            if value is not None
        }

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
                idempotency=self.config.idempotency,
                payload_mode=self.config.payload_mode,
            )
            rows_by_table.setdefault(table, []).append(row)
        return rows_by_table

    def _write_sql_for_table(self, table: str) -> OracleWriteSql:
        sql = build_write_sql(
            table=table,
            columns=self.config.columns,
            mode=self.config.mode,
            key_columns=self.config.idempotency.columns,
        )
        self._write_sql_cache[sql.table_name] = sql
        return sql

    def _write_rows_sync(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        pool = self._require_pool()
        with pool.acquire() as connection:
            self._prepare_connection_sync(connection)
            with connection.cursor() as cursor:
                for table, rows in rows_by_table.items():
                    if not rows:
                        continue
                    sql = self._write_sql_cache.get(table)
                    if sql is None:
                        sql = self._write_sql_for_table(table)
                    cursor.executemany(sql.sql, rows)
            try:
                connection.commit()
            except Exception as exc:
                raise DestinationUnavailableError("Oracle commit failed") from exc

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
        if code in {"ORA-01017", "ORA-00942", "ORA-00904"}:
            return PermanentSinkError(f"{context}: {code}")
        if code == "ORA-00001":
            return PermanentSinkError(f"{context}: duplicate key")
        return DestinationUnavailableError(f"{context}: {code or type(exc).__name__}")
