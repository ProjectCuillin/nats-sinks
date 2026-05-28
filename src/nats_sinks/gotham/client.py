# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Client boundary for Palantir Gotham RevDB object creation."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib import error, parse, request

from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
    SerializationError,
)
from nats_sinks.core.payload import load_standard_json
from nats_sinks.gotham.config import GothamSinkConfig
from nats_sinks.gotham.mapping import GothamObjectWrite

_USER_AGENT = "nats-sinks-gotham/0.4"
_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_PERMANENT_HTTP_STATUS = frozenset({400, 401, 403, 404, 410, 413, 415, 422})
_CONFLICT_HTTP_STATUS = 409
_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300
_CONTROL_CHARACTER_CUTOFF = 32
_ASCII_DELETE = 127


@dataclass(frozen=True, slots=True)
class GothamObjectWriteResult:
    """Sanitized summary returned by a Gotham object client."""

    accepted_objects: int
    duplicate_objects: int = 0
    rejected_objects: int = 0
    response_status: int | None = None


class GothamObjectClient(Protocol):
    """Protocol implemented by real and fake Gotham object clients."""

    async def create_objects(
        self,
        objects: Sequence[GothamObjectWrite],
        *,
        timeout_seconds: float,
    ) -> GothamObjectWriteResult:
        """Create Gotham objects and return a sanitized acceptance summary."""


class HttpGothamObjectClient:
    """HTTP client for Gotham RevDB object creation."""

    def __init__(self, config: GothamSinkConfig) -> None:
        self.config = config
        self._cached_token: str | None = None
        self._cached_token_expires_at: float = 0.0

    async def create_objects(
        self,
        objects: Sequence[GothamObjectWrite],
        *,
        timeout_seconds: float,
    ) -> GothamObjectWriteResult:
        """POST object-create requests to the configured Gotham endpoint."""

        return await asyncio.to_thread(
            self._create_objects_sync,
            tuple(objects),
            timeout_seconds,
        )

    def _create_objects_sync(
        self,
        objects: Sequence[GothamObjectWrite],
        timeout_seconds: float,
    ) -> GothamObjectWriteResult:
        token = self._access_token(timeout_seconds=timeout_seconds)
        accepted = 0
        duplicates = 0
        status: int | None = None
        for prepared in objects:
            request_obj = request.Request(  # noqa: S310 - URL is config-validated HTTPS/loopback.
                self.config.object_create_url(),
                data=_json_bytes(prepared.request),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": _USER_AGENT,
                },
                method="POST",
            )
            try:
                response_body, status = self._send_with_retries(
                    request_obj,
                    timeout_seconds=timeout_seconds,
                )
            except _DuplicateGothamObjectError:
                duplicates += 1
                continue
            _parse_create_object_response(response_body)
            accepted += 1
        return GothamObjectWriteResult(
            accepted_objects=accepted,
            duplicate_objects=duplicates,
            response_status=status,
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
        client_id = _secret_from_env(client_id_env, field="oauth2_client_id_env")
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
        timeout_seconds: float,
    ) -> tuple[bytes, int]:
        attempts = self.config.max_retries + 1
        last_error: DestinationUnavailableError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._urlopen(request_obj, timeout_seconds=timeout_seconds)
            except _DuplicateGothamObjectError:
                raise
            except DestinationUnavailableError as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                if self.config.retry_backoff_seconds > 0:
                    time.sleep(self.config.retry_backoff_seconds)
        if last_error is not None:
            raise last_error
        raise DestinationUnavailableError("Gotham object create failed without a response")

    def _urlopen(
        self,
        request_obj: request.Request,
        *,
        timeout_seconds: float,
    ) -> tuple[bytes, int]:
        try:
            with request.urlopen(  # nosec B310 # noqa: S310 - URLs are config-validated.
                request_obj,
                timeout=timeout_seconds,
            ) as response:
                status = int(getattr(response, "status", response.getcode()))
                response_body = response.read(self.config.max_response_bytes + 1)
        except error.HTTPError as exc:
            status = int(exc.code)
            if status == _CONFLICT_HTTP_STATUS and self.config.treat_conflict_as_duplicate:
                raise _DuplicateGothamObjectError from None
            if status in _RETRYABLE_HTTP_STATUS:
                raise DestinationUnavailableError(
                    f"Gotham object create returned retryable HTTP {status}"
                ) from None
            if status in _PERMANENT_HTTP_STATUS or status == _CONFLICT_HTTP_STATUS:
                raise PermanentSinkError(
                    f"Gotham object create returned permanent HTTP {status}"
                ) from None
            raise DestinationUnavailableError(
                f"Gotham object create returned unexpected HTTP {status}"
            ) from None
        except TimeoutError as exc:
            raise DestinationUnavailableError("Gotham object create timed out") from exc
        except OSError as exc:
            raise DestinationUnavailableError(
                "Gotham object create failed before response"
            ) from exc

        if len(response_body) > self.config.max_response_bytes:
            raise DestinationUnavailableError("Gotham object create response exceeded size limit")
        if status in _RETRYABLE_HTTP_STATUS:
            raise DestinationUnavailableError(
                f"Gotham object create returned retryable HTTP {status}"
            )
        if status in _PERMANENT_HTTP_STATUS or status == _CONFLICT_HTTP_STATUS:
            raise PermanentSinkError(f"Gotham object create returned permanent HTTP {status}")
        if status < _HTTP_SUCCESS_MIN or status >= _HTTP_SUCCESS_MAX:
            raise DestinationUnavailableError(
                f"Gotham object create returned unexpected HTTP {status}"
            )
        return response_body, status


class _DuplicateGothamObjectError(Exception):
    """Internal signal for opt-in duplicate conflict handling."""


def _secret_from_env(name: str, *, field: str) -> str:
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
        raise SerializationError("Gotham request body is not JSON serializable") from exc
    return rendered.encode("utf-8")


def _parse_oauth2_token_response(response_body: bytes) -> tuple[str, int]:
    try:
        loaded = load_standard_json(response_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise DestinationUnavailableError("Gotham OAuth2 token response is invalid JSON") from exc
    if not isinstance(loaded, dict):
        raise DestinationUnavailableError("Gotham OAuth2 token response must be a JSON object")
    access_token = loaded.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise DestinationUnavailableError("Gotham OAuth2 token response omitted access_token")
    expires_in = loaded.get("expires_in", 300)
    if isinstance(expires_in, bool) or not isinstance(expires_in, int | float):
        raise DestinationUnavailableError("Gotham OAuth2 token response has invalid expires_in")
    return access_token, max(int(expires_in), 0)


def _parse_create_object_response(response_body: bytes) -> str:
    if not response_body:
        raise DestinationUnavailableError("Gotham object create response omitted primaryKey")
    try:
        loaded = load_standard_json(response_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise DestinationUnavailableError("Gotham object create response is invalid JSON") from exc
    if not isinstance(loaded, dict):
        raise DestinationUnavailableError("Gotham object create response must be a JSON object")
    primary_key = loaded.get("primaryKey")
    if not isinstance(primary_key, str) or not primary_key.strip():
        raise DestinationUnavailableError("Gotham object create response omitted primaryKey")
    return primary_key
