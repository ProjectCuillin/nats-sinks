# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle NoSQL Database sink implementation.

``OracleNoSqlSink`` writes normalized event rows to one configured Oracle NoSQL
Database table.  A successful ``write_batch`` return means every selected SDK
put or conditional put completed with an unambiguous success or a configured
redelivery-safe duplicate result.  Any timeout, SDK failure, serialization
failure, or ambiguous result is raised as a framework error so the core runner
can avoid ACKing uncertain durable state.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, cast

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
from nats_sinks.oracle_nosql.config import OracleNoSqlSinkConfig
from nats_sinks.oracle_nosql.mapping import (
    oracle_nosql_create_table_statement,
    oracle_nosql_row_for_envelope,
)

LOGGER = logging.getLogger(__name__)


class OracleNoSqlClient(Protocol):
    """Minimal async adapter boundary used by ``OracleNoSqlSink``."""

    async def ensure_table(self) -> None:
        """Create or validate the configured table when enabled."""

    async def put_row(self, row: dict[str, Any], *, if_absent: bool) -> bool:
        """Write one row and return false only for conditional duplicate conflict."""

    async def close(self) -> None:
        """Close the underlying Oracle NoSQL SDK handle."""


OracleNoSqlClientFactory = Callable[
    [OracleNoSqlSinkConfig],
    Awaitable[OracleNoSqlClient] | OracleNoSqlClient,
]


class OracleNoSqlSink:
    """Write NATS envelopes to Oracle NoSQL Database."""

    def __init__(
        self,
        *,
        endpoint: str = "127.0.0.1:8080",
        deployment_mode: str = "kvstore",
        auth_mode: str | None = None,
        table_name: str = "nats_sinks_events",
        key_field: str = "sink_key",
        value_field: str = "event_json",
        stored_at_field: str = "stored_at_epoch_ns",
        namespace: str | None = None,
        compartment_id: str | None = None,
        cloudsim_tenant_id: str = "cloudsim",
        oci_config_file: str | None = None,
        oci_profile: str = "DEFAULT",
        oci_private_key_passphrase_env: str | None = None,
        key_strategy: str = "idempotency_key",
        key_prefix: str | None = None,
        duplicate_policy: str = "skip_existing",
        payload_mode: PayloadStorageMode = "json_or_envelope",
        auto_create: bool = False,
        read_units: int = 10,
        write_units: int = 10,
        storage_gb: int = 1,
        table_timeout_ms: int = 50_000,
        table_poll_interval_ms: int = 3_000,
        max_key_bytes: int = 512,
        max_value_bytes: int = 1_048_576,
        request_timeout_seconds: float = 10.0,
        config: OracleNoSqlSinkConfig | None = None,
        client_factory: OracleNoSqlClientFactory | None = None,
    ) -> None:
        if config is None:
            try:
                config = OracleNoSqlSinkConfig.model_validate(
                    {
                        "type": "oracle_nosql",
                        "endpoint": endpoint,
                        "deployment_mode": deployment_mode,
                        "auth_mode": auth_mode,
                        "table_name": table_name,
                        "key_field": key_field,
                        "value_field": value_field,
                        "stored_at_field": stored_at_field,
                        "namespace": namespace,
                        "compartment_id": compartment_id,
                        "cloudsim_tenant_id": cloudsim_tenant_id,
                        "oci_config_file": oci_config_file,
                        "oci_profile": oci_profile,
                        "oci_private_key_passphrase_env": oci_private_key_passphrase_env,
                        "key_strategy": key_strategy,
                        "key_prefix": key_prefix,
                        "duplicate_policy": duplicate_policy,
                        "payload_mode": payload_mode,
                        "auto_create": auto_create,
                        "read_units": read_units,
                        "write_units": write_units,
                        "storage_gb": storage_gb,
                        "table_timeout_ms": table_timeout_ms,
                        "table_poll_interval_ms": table_poll_interval_ms,
                        "max_key_bytes": max_key_bytes,
                        "max_value_bytes": max_value_bytes,
                        "request_timeout_seconds": request_timeout_seconds,
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self._client_factory = client_factory
        self._client: OracleNoSqlClient | None = None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> OracleNoSqlSink:
        """Create an Oracle NoSQL Database sink from raw sink configuration."""

        try:
            config = OracleNoSqlSinkConfig.model_validate(mapping)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(config=config)

    async def start(self) -> None:
        """Open the Oracle NoSQL SDK handle and optionally ensure the table."""

        if self._client is not None:
            return
        try:
            client = await asyncio.wait_for(
                self._create_client(),
                timeout=self.config.request_timeout_seconds,
            )
            if self.config.auto_create:
                await asyncio.wait_for(
                    client.ensure_table(),
                    timeout=self.config.request_timeout_seconds,
                )
        except ConfigurationError:
            raise
        except TimeoutError as exc:
            raise DestinationUnavailableError("Oracle NoSQL Database startup timed out") from exc
        except Exception as exc:
            raise DestinationUnavailableError("Oracle NoSQL Database startup failed") from exc
        self._client = client

    async def healthcheck(self) -> None:
        """Verify that the sink has an open client handle."""

        if self._client is None:
            raise DestinationUnavailableError("Oracle NoSQL Database sink is not started")

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write every message to Oracle NoSQL Database before returning success."""

        if not messages:
            return
        if self._client is None:
            raise DestinationUnavailableError("Oracle NoSQL Database sink is not started")
        try:
            for message in messages:
                row = oracle_nosql_row_for_envelope(message, config=self.config)
                await asyncio.wait_for(
                    self._write_one(row),
                    timeout=self.config.request_timeout_seconds,
                )
        except (SerializationError, PermanentSinkError, TemporarySinkError):
            raise
        except TimeoutError as exc:
            raise DestinationUnavailableError("Oracle NoSQL Database write timed out") from exc
        except Exception as exc:
            raise DestinationUnavailableError("Oracle NoSQL Database batch write failed") from exc

    async def stop(self) -> None:
        """Close the Oracle NoSQL SDK handle when one was opened."""

        client = self._client
        self._client = None
        if client is None:
            return
        await client.close()

    async def _create_client(self) -> OracleNoSqlClient:
        if self._client_factory is not None:
            client = await _maybe_await(self._client_factory(self.config))
            return cast(OracleNoSqlClient, client)
        return _BorneoOracleNoSqlClient.from_config(self.config)

    async def _write_one(self, row: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            raise DestinationUnavailableError("Oracle NoSQL Database sink is not started")

        if self.config.duplicate_policy == "replace":
            success = await client.put_row(row, if_absent=False)
            if not success:
                raise DestinationUnavailableError(
                    "Oracle NoSQL Database replace returned an ambiguous failure"
                )
            return

        success = await client.put_row(row, if_absent=True)
        if success:
            return
        if self.config.duplicate_policy == "skip_existing":
            LOGGER.debug("Oracle NoSQL Database duplicate key skipped")
            return
        raise PermanentSinkError("Oracle NoSQL Database destination key already exists")


class _BorneoOracleNoSqlClient:
    """Thin adapter around the official Oracle NoSQL Python SDK."""

    def __init__(
        self,
        *,
        config: OracleNoSqlSinkConfig,
        borneo: Any,
        handle: Any,
    ) -> None:
        self.config = config
        self._borneo = borneo
        self._handle = handle

    @classmethod
    def from_config(cls, config: OracleNoSqlSinkConfig) -> _BorneoOracleNoSqlClient:
        """Create a client adapter from safe validated configuration."""

        try:
            borneo = importlib.import_module("borneo")
        except ImportError as exc:
            raise ConfigurationError(
                "install nats-sinks[oracle-nosql] to use OracleNoSqlSink"
            ) from exc

        provider = _build_authorization_provider(borneo=borneo, config=config)
        handle_config = borneo.NoSQLHandleConfig(config.endpoint, provider)
        _maybe_call(handle_config, "set_default_namespace", config.namespace)
        handle = borneo.NoSQLHandle(handle_config)
        return cls(config=config, borneo=borneo, handle=handle)

    async def ensure_table(self) -> None:
        """Create the configured table using generated safe DDL."""

        statement = oracle_nosql_create_table_statement(config=self.config)
        table_request = self._borneo.TableRequest().set_statement(statement)
        if self.config.deployment_mode == "cloud":
            table_limits = self._borneo.TableLimits(
                self.config.read_units,
                self.config.write_units,
                self.config.storage_gb,
            )
            table_request.set_table_limits(table_limits)

        result: Any
        do_table_request = getattr(self._handle, "do_table_request", None)
        if callable(do_table_request):
            result = do_table_request(
                table_request,
                self.config.table_timeout_ms,
                self.config.table_poll_interval_ms,
            )
        else:
            result = self._handle.table_request(table_request)
            wait_for_completion = getattr(result, "wait_for_completion", None)
            if callable(wait_for_completion):
                wait_for_completion(
                    self._handle,
                    self.config.table_timeout_ms,
                    self.config.table_poll_interval_ms,
                )
        if inspect.isawaitable(result):
            await result

    async def put_row(self, row: dict[str, Any], *, if_absent: bool) -> bool:
        """Put one row through the SDK and return explicit success."""

        request = self._borneo.PutRequest().set_table_name(self.config.table_name).set_value(row)
        _maybe_call(request, "set_timeout", int(self.config.request_timeout_seconds * 1000))
        if if_absent:
            request.set_option(self._borneo.PutOption.IF_ABSENT)
            _maybe_call(request, "set_return_row", False)
        result = self._handle.put(request)
        if inspect.isawaitable(result):
            result = await result
        return _put_result_succeeded(result)

    async def close(self) -> None:
        """Close the SDK handle."""

        close = getattr(self._handle, "close", None)
        if close is None or not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result


def _build_authorization_provider(
    *,
    borneo: Any,
    config: OracleNoSqlSinkConfig,
) -> Any:
    if config.auth_mode == "store_access_token":
        kv_module = importlib.import_module("borneo.kv")
        return kv_module.StoreAccessTokenProvider()
    if config.auth_mode == "cloudsim":
        return _cloudsim_authorization_provider(borneo, tenant_id=config.cloudsim_tenant_id)
    iam_module = importlib.import_module("borneo.iam")
    if config.auth_mode == "instance_principal":
        return iam_module.SignatureProvider.create_with_instance_principal()
    passphrase = (
        os.environ.get(config.oci_private_key_passphrase_env)
        if config.oci_private_key_passphrase_env is not None
        else None
    )
    kwargs: dict[str, Any] = {"profile_name": config.oci_profile}
    if config.oci_config_file is not None:
        kwargs["config_file"] = config.oci_config_file
    if passphrase is not None:
        kwargs["pass_phrase"] = passphrase
    return iam_module.SignatureProvider(**kwargs)


def _cloudsim_authorization_provider(borneo: Any, *, tenant_id: str) -> Any:
    """Create the SDK cloud-simulator provider without storing secrets."""

    class CloudsimAuthorizationProvider(borneo.AuthorizationProvider):  # type: ignore[misc]
        def close(self) -> None:
            return None

        def get_authorization_string(self, request: object | None = None) -> str:
            _ = request
            return f"Bearer {tenant_id}"

    return CloudsimAuthorizationProvider()


def _put_result_succeeded(result: Any) -> bool:
    """Interpret SDK put results without treating ambiguity as success."""

    if isinstance(result, bool):
        return result
    for method_name in ("get_success", "is_success"):
        method = getattr(result, method_name, None)
        if callable(method):
            return bool(method())
    for attribute_name in ("success", "succeeded"):
        if hasattr(result, attribute_name):
            return bool(getattr(result, attribute_name))
    get_version = getattr(result, "get_version", None)
    if callable(get_version):
        return get_version() is not None
    version = getattr(result, "version", None)
    if version is not None:
        return True
    raise DestinationUnavailableError("Oracle NoSQL Database put returned no success indicator")


def _maybe_call(target: Any, method_name: str, value: Any) -> None:
    if value is None:
        return
    method = getattr(target, method_name, None)
    if method is not None and callable(method):
        method(value)


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
