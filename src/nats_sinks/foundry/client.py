# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Client boundary for Palantir Foundry Streams push ingestion.

The sink depends on this small protocol rather than a broad SDK surface.  Unit
tests can provide a fake client, while live deployments can use the standard
library HTTP implementation below.  The HTTP client intentionally reports only
sanitized status summaries; endpoint URLs, tokens, client identifiers, and
response bodies are never included in framework error messages.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib import error, parse, request

from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
    SerializationError,
)
from nats_sinks.core.payload import load_standard_json
from nats_sinks.foundry.config import FoundrySinkConfig

_USER_AGENT = "nats-sinks-foundry/0.4"
_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_PERMANENT_HTTP_STATUS = frozenset({400, 401, 403, 404, 409, 410, 413, 415, 422})
_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300
_CONTROL_CHARACTER_CUTOFF = 32
_ASCII_DELETE = 127


@dataclass(frozen=True, slots=True)
class FoundryStreamPushResult:
    """Sanitized summary returned by a Foundry stream push client."""

    accepted_records: int
    duplicate_records: int = 0
    rejected_records: int = 0
    response_status: int | None = None


class FoundryStreamClient(Protocol):
    """Protocol implemented by real and fake Foundry stream clients."""

    async def push_records(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        timeout_seconds: float,
    ) -> FoundryStreamPushResult:
        """Push records into Foundry and return a sanitized acceptance summary."""


class HttpFoundryStreamClient:
    """HTTP client for Foundry Streams push ingestion."""

    def __init__(self, config: FoundrySinkConfig) -> None:
        self.config = config
        self._cached_token: str | None = None
        self._cached_token_expires_at: float = 0.0

    async def push_records(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        timeout_seconds: float,
    ) -> FoundryStreamPushResult:
        """POST records to the configured Foundry stream endpoint."""

        return await asyncio.to_thread(
            self._push_records_sync,
            tuple(records),
            timeout_seconds,
        )

    def _push_records_sync(
        self,
        records: Sequence[Mapping[str, Any]],
        timeout_seconds: float,
    ) -> FoundryStreamPushResult:
        token = self._access_token(timeout_seconds=timeout_seconds)
        body = _json_bytes(records)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        request_obj = request.Request(  # noqa: S310 - URL is config-validated HTTPS/loopback.
            self.config.stream_push_url,
            data=body,
            headers=headers,
            method="POST",
        )
        return self._send_with_retries(
            request_obj,
            record_count=len(records),
            timeout_seconds=timeout_seconds,
        )

    def _access_token(self, *, timeout_seconds: float) -> str:
        if self.config.auth_mode == "bearer_token_env":
            bearer_token_env = self.config.bearer_token_env
            if bearer_token_env is None:
                raise ConfigurationError("bearer_token_env is required")
            return _secret_from_env(bearer_token_env, field="bearer_token_env")

        now = time.monotonic()
        if self._cached_token and now < self._cached_token_expires_at:
            return self._cached_token
        token_url = self.config.oauth2_token_url
        client_id_env = self.config.oauth2_client_id_env
        client_secret_env = self.config.oauth2_client_secret_env
        if token_url is None or client_id_env is None or client_secret_env is None:
            raise ConfigurationError("OAuth2 client credentials configuration is incomplete")
        client_id = _secret_from_env(
            client_id_env,
            field="oauth2_client_id_env",
        )
        client_secret = _secret_from_env(
            client_secret_env,
            field="oauth2_client_secret_env",
        )
        form: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if self.config.oauth2_scope is not None:
            form["scope"] = self.config.oauth2_scope
        body = parse.urlencode(form).encode("utf-8")
        request_obj = request.Request(  # noqa: S310 - URL is config-validated HTTPS/loopback.
            token_url,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        response_body, _status = self._urlopen(request_obj, timeout_seconds=timeout_seconds)
        token, expires_in = _parse_oauth2_token_response(response_body)
        self._cached_token = token
        self._cached_token_expires_at = now + max(expires_in - 30, 0)
        return token

    def _send_with_retries(
        self,
        request_obj: request.Request,
        *,
        record_count: int,
        timeout_seconds: float,
    ) -> FoundryStreamPushResult:
        attempts = self.config.max_retries + 1
        last_error: DestinationUnavailableError | None = None
        for attempt in range(1, attempts + 1):
            try:
                response_body, status = self._urlopen(request_obj, timeout_seconds=timeout_seconds)
                return _parse_push_response(
                    response_body,
                    status=status,
                    default_record_count=record_count,
                )
            except DestinationUnavailableError as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                if self.config.retry_backoff_seconds > 0:
                    time.sleep(self.config.retry_backoff_seconds)
        if last_error is not None:
            raise last_error
        raise DestinationUnavailableError("Foundry stream push failed without a response")

    def _urlopen(
        self,
        request_obj: request.Request,
        *,
        timeout_seconds: float,
    ) -> tuple[bytes, int]:
        try:
            with request.urlopen(request_obj, timeout=timeout_seconds) as response:  # noqa: S310
                status = int(getattr(response, "status", response.getcode()))
                response_body = response.read(self.config.max_response_bytes + 1)
        except error.HTTPError as exc:
            status = int(exc.code)
            if status in _RETRYABLE_HTTP_STATUS:
                raise DestinationUnavailableError(
                    f"Foundry stream push returned retryable HTTP {status}"
                ) from None
            if status in _PERMANENT_HTTP_STATUS:
                raise PermanentSinkError(
                    f"Foundry stream push returned permanent HTTP {status}"
                ) from None
            raise DestinationUnavailableError(
                f"Foundry stream push returned unexpected HTTP {status}"
            ) from None
        except TimeoutError as exc:
            raise DestinationUnavailableError("Foundry stream push timed out") from exc
        except OSError as exc:
            raise DestinationUnavailableError("Foundry stream push failed before response") from exc

        if len(response_body) > self.config.max_response_bytes:
            raise DestinationUnavailableError("Foundry stream push response exceeded size limit")
        if status in _RETRYABLE_HTTP_STATUS:
            raise DestinationUnavailableError(
                f"Foundry stream push returned retryable HTTP {status}"
            )
        if status in _PERMANENT_HTTP_STATUS:
            raise PermanentSinkError(f"Foundry stream push returned permanent HTTP {status}")
        if status < _HTTP_SUCCESS_MIN or status >= _HTTP_SUCCESS_MAX:
            raise DestinationUnavailableError(
                f"Foundry stream push returned unexpected HTTP {status}"
            )
        return response_body, status


def _secret_from_env(name: str, *, field: str) -> str:
    """Return a non-empty secret from the environment without logging it."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ConfigurationError(f"{field} environment variable is not set")
    if any(
        ord(character) < _CONTROL_CHARACTER_CUTOFF or ord(character) == _ASCII_DELETE
        for character in value
    ):
        raise ConfigurationError(f"{field} environment variable contains control characters")
    return value


def _json_bytes(value: object) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError("Foundry request body is not JSON serializable") from exc
    return rendered.encode("utf-8")


def _parse_oauth2_token_response(response_body: bytes) -> tuple[str, int]:
    try:
        loaded = load_standard_json(response_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise DestinationUnavailableError("Foundry OAuth2 token response is invalid JSON") from exc
    if not isinstance(loaded, dict):
        raise DestinationUnavailableError("Foundry OAuth2 token response must be a JSON object")
    access_token = loaded.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise DestinationUnavailableError("Foundry OAuth2 token response omitted access_token")
    expires_in = loaded.get("expires_in", 300)
    if isinstance(expires_in, bool) or not isinstance(expires_in, int | float):
        raise DestinationUnavailableError("Foundry OAuth2 token response has invalid expires_in")
    return access_token, max(int(expires_in), 0)


def _parse_push_response(
    response_body: bytes,
    *,
    status: int,
    default_record_count: int,
) -> FoundryStreamPushResult:
    if not response_body:
        return FoundryStreamPushResult(
            accepted_records=default_record_count,
            response_status=status,
        )
    try:
        loaded = load_standard_json(response_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise DestinationUnavailableError("Foundry stream push response is invalid JSON") from exc
    if not isinstance(loaded, dict):
        raise DestinationUnavailableError("Foundry stream push response must be a JSON object")
    accepted = _optional_non_negative_int(loaded, "accepted_records", default_record_count)
    duplicate = _optional_non_negative_int(loaded, "duplicate_records", 0)
    rejected = _optional_non_negative_int(loaded, "rejected_records", 0)
    return FoundryStreamPushResult(
        accepted_records=accepted,
        duplicate_records=duplicate,
        rejected_records=rejected,
        response_status=status,
    )


def _optional_non_negative_int(
    mapping: Mapping[str, Any],
    field: str,
    default: int,
) -> int:
    value = mapping.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DestinationUnavailableError(f"Foundry stream push response has invalid {field}")
    if value < 0:
        raise DestinationUnavailableError(f"Foundry stream push response has negative {field}")
    return cast(int, value)
