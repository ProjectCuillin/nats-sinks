# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""First-party S3-compatible object sink."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    NatsSinksError,
    PermanentSinkError,
)
from nats_sinks.core.payload import PayloadStorageMode
from nats_sinks.core.retry import RetryPolicy
from nats_sinks.s3.client import S3Client, S3PutObjectRequest, StandardS3Client
from nats_sinks.s3.config import S3SinkConfig
from nats_sinks.s3.mapping import (
    S3PreparedObject,
    prepare_s3_object,
    prepare_s3_sidecar_object,
)

S3ClientFactory = Callable[[S3SinkConfig], Awaitable[S3Client] | S3Client]


class S3Sink:
    """Write NATS envelopes to an S3-compatible object store."""

    def __init__(
        self,
        *,
        bucket: str = "nats-sinks-events",
        prefix: str | None = None,
        endpoint_url: str | None = None,
        allow_http_for_local_testing: bool = False,
        region_name: str | None = None,
        credential_mode: str = "default_chain",
        profile_name: str | None = None,
        aws_access_key_id_env: str | None = None,
        aws_secret_access_key_env: str | None = None,
        aws_session_token_env: str | None = None,
        key_strategy: str = "idempotency_key",
        key_prefix: str | None = None,
        object_suffix: str = ".json",
        duplicate_policy: str = "skip_existing",
        object_format: str = "envelope",
        metadata_mode: str = "object_metadata",
        sidecar_suffix: str = ".metadata.json",
        payload_mode: PayloadStorageMode = "json_or_envelope",
        compression: str = "none",
        content_type: str = "application/json",
        object_metadata: dict[str, str] | None = None,
        server_side_encryption: str = "none",
        max_key_bytes: int = 1024,
        max_object_bytes: int = 16_777_216,
        max_metadata_bytes: int = 4096,
        request_timeout_seconds: float = 10.0,
        max_retries: int = 0,
        retry_backoff_ms: int = 250,
        retry_max_backoff_ms: int = 5_000,
        retry_backoff_mode: str = "exponential",
        retry_backoff_multiplier: float = 2.0,
        retry_jitter: str = "full",
        config: S3SinkConfig | None = None,
        client_factory: S3ClientFactory | None = None,
    ) -> None:
        if config is None:
            try:
                config = S3SinkConfig.model_validate(
                    {
                        "type": "s3",
                        "bucket": bucket,
                        "prefix": prefix,
                        "endpoint_url": endpoint_url,
                        "allow_http_for_local_testing": allow_http_for_local_testing,
                        "region_name": region_name,
                        "credential_mode": credential_mode,
                        "profile_name": profile_name,
                        "aws_access_key_id_env": aws_access_key_id_env,
                        "aws_secret_access_key_env": aws_secret_access_key_env,
                        "aws_session_token_env": aws_session_token_env,
                        "key_strategy": key_strategy,
                        "key_prefix": key_prefix,
                        "object_suffix": object_suffix,
                        "duplicate_policy": duplicate_policy,
                        "object_format": object_format,
                        "metadata_mode": metadata_mode,
                        "sidecar_suffix": sidecar_suffix,
                        "payload_mode": payload_mode,
                        "compression": compression,
                        "content_type": content_type,
                        "object_metadata": object_metadata or {},
                        "server_side_encryption": server_side_encryption,
                        "max_key_bytes": max_key_bytes,
                        "max_object_bytes": max_object_bytes,
                        "max_metadata_bytes": max_metadata_bytes,
                        "request_timeout_seconds": request_timeout_seconds,
                        "max_retries": max_retries,
                        "retry_backoff_ms": retry_backoff_ms,
                        "retry_max_backoff_ms": retry_max_backoff_ms,
                        "retry_backoff_mode": retry_backoff_mode,
                        "retry_backoff_multiplier": retry_backoff_multiplier,
                        "retry_jitter": retry_jitter,
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self._client_factory = client_factory
        self._client: S3Client | None = None
        self._retry_policy = RetryPolicy(
            max_retries=config.max_retries,
            backoff_ms=config.retry_backoff_ms,
            max_backoff_ms=config.retry_max_backoff_ms,
            backoff_mode=config.retry_backoff_mode,
            backoff_multiplier=config.retry_backoff_multiplier,
            jitter=config.retry_jitter,
        )

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> S3Sink:
        """Create an S3 sink from raw JSON configuration."""

        try:
            config = S3SinkConfig.model_validate(mapping)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(config=config)

    async def start(self) -> None:
        """Create the S3 client boundary without writing any object."""

        if self._client is not None:
            return
        try:
            self._client = await _maybe_await(self._create_client())
        except ConfigurationError:
            raise
        except Exception as exc:
            raise DestinationUnavailableError("S3 sink startup failed") from exc

    async def healthcheck(self) -> None:
        """Verify that the sink has an initialized client."""

        if self._client is None:
            raise DestinationUnavailableError("S3 sink is not started")

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write every message object before returning durable success."""

        if not messages:
            return
        if self._client is None:
            raise DestinationUnavailableError("S3 sink is not started")
        for message in messages:
            await self._write_message_with_retries(message)

    async def stop(self) -> None:
        """Close the S3 client boundary when one was created."""

        client = self._client
        self._client = None
        if client is not None:
            await client.close()

    async def _create_client(self) -> S3Client:
        if self._client_factory is not None:
            return cast(S3Client, await _maybe_await(self._client_factory(self.config)))
        return StandardS3Client(config=self.config)

    async def _write_message_with_retries(self, message: NatsEnvelope) -> None:
        last_error: DestinationUnavailableError | None = None
        attempts = self.config.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                await self._write_message_once(message)
                return
            except DestinationUnavailableError as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                delay = self._retry_policy.backoff_seconds(attempt)
                if delay > 0:
                    await asyncio.sleep(delay)
            except NatsSinksError:
                raise
            except TimeoutError as exc:
                last_error = DestinationUnavailableError("S3 object write timed out")
                last_error.__cause__ = exc
                if attempt >= attempts:
                    break
                delay = self._retry_policy.backoff_seconds(attempt)
                if delay > 0:
                    await asyncio.sleep(delay)
            except Exception as exc:
                raise DestinationUnavailableError(
                    "S3 object write failed before durable success was confirmed"
                ) from exc

        if last_error is not None:
            raise last_error
        raise DestinationUnavailableError("S3 object write failed without a result")

    async def _write_message_once(self, message: NatsEnvelope) -> None:
        primary = prepare_s3_object(message, config=self.config)
        created = await self._put_prepared(primary, if_none_match=self._uses_conditional_put())
        if not created:
            if self.config.duplicate_policy == "fail_existing":
                raise PermanentSinkError("S3 object key already exists")
            if self.config.metadata_mode != "sidecar":
                return

        if self.config.metadata_mode == "sidecar":
            sidecar = prepare_s3_sidecar_object(
                message,
                config=self.config,
                object_key=primary.key,
            )
            await self._put_prepared(sidecar, if_none_match=self._uses_conditional_put())

    def _uses_conditional_put(self) -> bool:
        return self.config.duplicate_policy != "replace"

    async def _put_prepared(self, prepared: S3PreparedObject, *, if_none_match: bool) -> bool:
        client = self._client
        if client is None:
            raise DestinationUnavailableError("S3 sink is not started")
        request = S3PutObjectRequest(
            bucket=self.config.bucket,
            key=prepared.key,
            body=prepared.body,
            content_type=prepared.content_type,
            content_encoding=prepared.content_encoding,
            metadata=prepared.metadata,
            if_none_match=if_none_match,
            server_side_encryption=(
                self.config.server_side_encryption
                if self.config.server_side_encryption != "none"
                else None
            ),
        )
        return await asyncio.wait_for(
            client.put_object(request),
            timeout=self.config.request_timeout_seconds,
        )


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
