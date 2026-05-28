# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle Coherence Community Edition sink implementation.

``CoherenceSink`` writes one complete normalized event JSON object into a
configured Coherence map or cache per message.  Returning from ``write_batch``
means every selected write operation completed.  Any timeout, rejected write,
serialization failure, or ambiguous client failure is translated into a
framework error so the core runner can avoid ACKing uncertain durable state.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from nats_sinks.coherence.config import CoherenceSinkConfig
from nats_sinks.coherence.mapping import (
    coherence_key_for_envelope,
    coherence_value_for_envelope,
)
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
    SerializationError,
    TemporarySinkError,
)
from nats_sinks.core.payload import PayloadStorageMode

LOGGER = logging.getLogger(__name__)

CoherenceSessionFactory = Callable[[CoherenceSinkConfig], Awaitable[Any] | Any]


class CoherenceSink:
    """Write NATS envelopes into Oracle Coherence Community Edition."""

    def __init__(
        self,
        *,
        address: str = "127.0.0.1:1408",
        scope: str = "",
        cache_name: str = "nats_sinks_events",
        storage: str = "cache",
        serializer: str = "json",
        key_strategy: str = "idempotency_key",
        key_prefix: str | None = None,
        duplicate_policy: str = "skip_existing",
        payload_mode: PayloadStorageMode = "json_or_envelope",
        ttl_seconds: int | None = None,
        max_key_bytes: int = 512,
        max_value_bytes: int = 1_048_576,
        request_timeout_seconds: float = 10.0,
        ready_timeout_seconds: float = 30.0,
        session_disconnect_seconds: float = 30.0,
        config: CoherenceSinkConfig | None = None,
        session_factory: CoherenceSessionFactory | None = None,
    ) -> None:
        if config is None:
            try:
                config = CoherenceSinkConfig.model_validate(
                    {
                        "type": "coherence",
                        "address": address,
                        "scope": scope,
                        "cache_name": cache_name,
                        "storage": storage,
                        "serializer": serializer,
                        "key_strategy": key_strategy,
                        "key_prefix": key_prefix,
                        "duplicate_policy": duplicate_policy,
                        "payload_mode": payload_mode,
                        "ttl_seconds": ttl_seconds,
                        "max_key_bytes": max_key_bytes,
                        "max_value_bytes": max_value_bytes,
                        "request_timeout_seconds": request_timeout_seconds,
                        "ready_timeout_seconds": ready_timeout_seconds,
                        "session_disconnect_seconds": session_disconnect_seconds,
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self._session_factory = session_factory
        self._session: Any | None = None
        self._collection: Any | None = None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> CoherenceSink:
        """Create a Coherence sink from raw sink configuration."""

        try:
            config = CoherenceSinkConfig.model_validate(mapping)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(config=config)

    async def start(self) -> None:
        """Open a Coherence session and resolve the configured map/cache."""

        if self._collection is not None:
            return
        try:
            session = await asyncio.wait_for(
                self._create_session(),
                timeout=self.config.request_timeout_seconds,
            )
            collection = await asyncio.wait_for(
                self._open_collection(session),
                timeout=self.config.request_timeout_seconds,
            )
        except ConfigurationError:
            raise
        except TimeoutError as exc:
            raise DestinationUnavailableError("Oracle Coherence startup timed out") from exc
        except Exception as exc:
            raise DestinationUnavailableError("Oracle Coherence startup failed") from exc
        self._session = session
        self._collection = collection

    async def healthcheck(self) -> None:
        """Verify that the sink has an open collection handle."""

        if self._collection is None:
            raise DestinationUnavailableError("Oracle Coherence sink is not started")

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write every message to Coherence before returning success."""

        if not messages:
            return
        if self._collection is None:
            raise DestinationUnavailableError("Oracle Coherence sink is not started")
        try:
            for message in messages:
                key = coherence_key_for_envelope(message, config=self.config)
                value = coherence_value_for_envelope(message, config=self.config)
                await asyncio.wait_for(
                    self._write_one(key, value),
                    timeout=self.config.request_timeout_seconds,
                )
        except (SerializationError, PermanentSinkError, TemporarySinkError):
            raise
        except TimeoutError as exc:
            raise DestinationUnavailableError("Oracle Coherence write timed out") from exc
        except Exception as exc:
            raise DestinationUnavailableError("Oracle Coherence batch write failed") from exc

    async def stop(self) -> None:
        """Close the Coherence session when the client exposes a close method."""

        session = self._session
        self._collection = None
        self._session = None
        if session is None:
            return
        close = getattr(session, "close", None)
        if close is None or not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    async def _create_session(self) -> Any:
        if self._session_factory is not None:
            return await _maybe_await(self._session_factory(self.config))

        try:
            coherence = importlib.import_module("coherence")
        except ImportError as exc:
            raise ConfigurationError("install nats-sinks[coherence] to use CoherenceSink") from exc

        options = coherence.Options(
            address=self.config.address,
            scope=self.config.scope,
            request_timeout_seconds=self.config.request_timeout_seconds,
            ready_timeout_seconds=self.config.ready_timeout_seconds,
            session_disconnect_seconds=self.config.session_disconnect_seconds,
            ser_format=self.config.serializer,
        )
        return await _maybe_await(coherence.Session.create(options))

    async def _open_collection(self, session: Any) -> Any:
        method_name = "get_cache" if self.config.storage == "cache" else "get_map"
        method = getattr(session, method_name, None)
        if method is None or not callable(method):
            raise DestinationUnavailableError("Oracle Coherence session cannot open collection")
        return await _maybe_await(method(self.config.cache_name))

    async def _write_one(self, key: str, value: dict[str, Any]) -> None:
        if self.config.duplicate_policy == "replace":
            await self._put(key, value)
            return

        previous = await self._put_if_absent(key, value)
        if previous is None:
            return
        if self.config.duplicate_policy == "skip_existing":
            LOGGER.debug("Oracle Coherence duplicate key skipped")
            return
        raise PermanentSinkError("Oracle Coherence destination key already exists")

    async def _put(self, key: str, value: dict[str, Any]) -> Any:
        collection = self._collection
        if collection is None:
            raise DestinationUnavailableError("Oracle Coherence sink is not started")
        method = getattr(collection, "put", None)
        if method is None or not callable(method):
            raise DestinationUnavailableError("Oracle Coherence collection does not support put")
        if self.config.storage == "cache" and self.config.ttl_seconds is not None:
            return await _maybe_await(method(key, value, ttl=self.config.ttl_seconds))
        return await _maybe_await(method(key, value))

    async def _put_if_absent(self, key: str, value: dict[str, Any]) -> Any:
        collection = self._collection
        if collection is None:
            raise DestinationUnavailableError("Oracle Coherence sink is not started")
        method = getattr(collection, "put_if_absent", None)
        if method is None or not callable(method):
            raise DestinationUnavailableError(
                "Oracle Coherence collection does not support put_if_absent"
            )
        if self.config.storage == "cache" and self.config.ttl_seconds is not None:
            return await _maybe_await(method(key, value, ttl=self.config.ttl_seconds))
        return await _maybe_await(method(key, value))


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
