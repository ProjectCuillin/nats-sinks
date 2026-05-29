# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""S3-compatible client boundary used by the first-party S3 sink."""

from __future__ import annotations

import asyncio
import importlib
import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
)
from nats_sinks.s3.config import S3SinkConfig

_HTTP_CONDITIONAL_FAILURE = 412
_HTTP_SERVER_ERROR_MINIMUM = 500
_HTTP_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429})


@dataclass(frozen=True, slots=True)
class S3PutObjectRequest:
    """One S3-compatible put-object request."""

    bucket: str
    key: str
    body: bytes
    content_type: str
    content_encoding: str | None
    metadata: dict[str, str]
    if_none_match: bool
    server_side_encryption: str | None


@runtime_checkable
class S3Client(Protocol):
    """Minimal async S3-compatible client used by ``S3Sink``."""

    async def put_object(self, request: S3PutObjectRequest) -> bool:
        """Write an object.

        Return ``False`` only when an ``if_none_match`` conditional write found
        that the object already exists.  Raise a framework error for every
        ambiguous or failed destination result.
        """

    async def close(self) -> None:
        """Release any client resources."""


class StandardS3Client:
    """Thin async wrapper around boto3's S3 client."""

    def __init__(self, *, config: S3SinkConfig) -> None:
        self.config = config
        self._client = _build_boto3_client(config)

    async def put_object(self, request: S3PutObjectRequest) -> bool:
        """Write one object without exposing boto3 details to the sink."""

        return await asyncio.to_thread(self._put_object_sync, request)

    async def close(self) -> None:
        """Close the underlying boto3 client if the installed version supports it."""

        close = getattr(self._client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)

    def _put_object_sync(self, request: S3PutObjectRequest) -> bool:
        params: dict[str, Any] = {
            "Bucket": request.bucket,
            "Key": request.key,
            "Body": request.body,
            "ContentType": request.content_type,
            "Metadata": request.metadata,
        }
        if request.content_encoding is not None:
            params["ContentEncoding"] = request.content_encoding
        if request.if_none_match:
            params["IfNoneMatch"] = "*"
        if request.server_side_encryption is not None:
            params["ServerSideEncryption"] = request.server_side_encryption

        try:
            self._client.put_object(**params)
        except Exception as exc:
            if _is_conditional_duplicate(exc):
                return False
            raise _translate_boto_exception(exc) from exc
        return True


def _build_boto3_client(config: S3SinkConfig) -> Any:
    """Create a boto3 S3 client from validated non-secret configuration."""

    try:
        boto3 = importlib.import_module("boto3")
        botocore_config = importlib.import_module("botocore.config")
    except ImportError as exc:
        raise ConfigurationError("install nats-sinks[s3] to use S3Sink") from exc

    session_kwargs: dict[str, Any] = {}
    if config.region_name is not None:
        session_kwargs["region_name"] = config.region_name
    if config.credential_mode == "profile":
        session_kwargs["profile_name"] = config.profile_name
    elif config.credential_mode == "environment":
        session_kwargs["aws_access_key_id"] = _env_value(
            config.aws_access_key_id_env,
            field="aws_access_key_id_env",
        )
        session_kwargs["aws_secret_access_key"] = _env_value(
            config.aws_secret_access_key_env,
            field="aws_secret_access_key_env",
        )
        if config.aws_session_token_env is not None:
            session_kwargs["aws_session_token"] = _env_value(
                config.aws_session_token_env,
                field="aws_session_token_env",
            )

    session = boto3.Session(**session_kwargs)
    client_config = botocore_config.Config(
        connect_timeout=config.request_timeout_seconds,
        read_timeout=config.request_timeout_seconds,
        retries={"max_attempts": 1, "mode": "standard"},
    )
    return session.client(
        "s3",
        endpoint_url=config.endpoint_url,
        config=client_config,
    )


def _env_value(name: str | None, *, field: str) -> str:
    if name is None:
        raise ConfigurationError(f"sink.{field} is required for environment credentials")
    value = os.environ.get(name)
    if value is None or not value.strip() or value.strip() != value:
        raise ConfigurationError(f"sink.{field} references a missing or invalid value")
    return value


def _is_conditional_duplicate(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error", {})
    metadata = response.get("ResponseMetadata", {})
    code = str(error.get("Code", ""))
    status_code = metadata.get("HTTPStatusCode")
    return status_code == _HTTP_CONDITIONAL_FAILURE or code in {
        "PreconditionFailed",
        "ConditionalRequestConflict",
    }


def _translate_boto_exception(exc: BaseException) -> Exception:
    """Translate boto3/botocore failures into framework errors."""

    name = exc.__class__.__name__
    if name in {"NoCredentialsError", "PartialCredentialsError", "ProfileNotFound"}:
        return ConfigurationError("S3 credentials are not available through the configured mode")
    if name in {
        "ConnectTimeoutError",
        "ConnectionClosedError",
        "EndpointConnectionError",
        "ReadTimeoutError",
    }:
        return DestinationUnavailableError("S3 destination did not complete the request")

    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if isinstance(status_code, int):
            if (
                status_code >= _HTTP_SERVER_ERROR_MINIMUM
                or status_code in _HTTP_RETRYABLE_STATUS_CODES
            ):
                return DestinationUnavailableError("S3 destination returned a retryable status")
            return PermanentSinkError("S3 destination rejected the object request")
    return DestinationUnavailableError("S3 destination failed before durable success was confirmed")
