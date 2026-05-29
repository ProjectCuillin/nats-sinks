# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""First-party HTTP sink.

``HttpSink`` forwards normalized NATS envelopes to one configured HTTP
endpoint.  The core runner remains the only component that can ACK JetStream
messages; the sink returns success only after the endpoint has returned one of
the configured success status codes for every message in the batch.

HTTP destinations are inherently varied.  Operators should use endpoints that
honor the propagated idempotency key and should treat client-side timeouts as
ambiguous remote outcomes.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    NatsSinksError,
    PermanentSinkError,
)
from nats_sinks.core.retry import RetryPolicy
from nats_sinks.http.client import HttpClient, HttpRequest, HttpResponse, StandardHttpClient
from nats_sinks.http.config import HttpSinkConfig
from nats_sinks.http.mapping import prepare_http_body

_CONTROL_CHARACTER_CUTOFF = 32
_ASCII_DELETE = 127


class HttpSink:
    """Write NATS envelopes to a fixed HTTP endpoint."""

    def __init__(
        self,
        *,
        url: str,
        config: HttpSinkConfig | None = None,
        client: HttpClient | None = None,
        **config_values: Any,
    ) -> None:
        if config is None:
            try:
                config = HttpSinkConfig.model_validate(
                    {
                        "type": "http",
                        "url": url,
                        **config_values,
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self._client = client
        self._retry_policy = RetryPolicy(
            max_retries=self.config.retry.max_retries,
            backoff_ms=self.config.retry.backoff_ms,
            max_backoff_ms=self.config.retry.max_backoff_ms,
            backoff_mode=self.config.retry.backoff_mode,
            backoff_multiplier=self.config.retry.backoff_multiplier,
            jitter=self.config.retry.jitter,
        )

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> HttpSink:
        """Create an HTTP sink from raw JSON configuration."""

        try:
            config = HttpSinkConfig.model_validate(mapping)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(url=config.url, config=config)

    async def start(self) -> None:
        """Prepare the HTTP client.

        No network call is made during startup.  That keeps CLI validation and
        service bootstrapping free of side effects against the destination.
        """

        if self._client is None:
            self._client = StandardHttpClient()

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Send every message in the batch before returning success."""

        if not messages:
            return
        client = self._client
        if client is None:
            client = StandardHttpClient()
            self._client = client
        for message in messages:
            request = self._request_for_envelope(message)
            await self._send_with_retries(client, request)

    async def stop(self) -> None:
        """Release resources.

        The standard HTTP client opens request-scoped connections only.
        """

    def _request_for_envelope(self, envelope: NatsEnvelope) -> HttpRequest:
        prepared = prepare_http_body(envelope, config=self.config)
        headers = self._base_headers()
        if prepared.idempotency_key is not None:
            headers[self.config.idempotency.header] = prepared.idempotency_key
        return HttpRequest(
            method=self.config.method,
            url=self.config.url,
            headers=headers,
            body=prepared.body,
        )

    def _base_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self.config.user_agent,
        }
        headers.update(self.config.headers)
        for name, env_name in self.config.headers_env.items():
            headers[name] = _header_value_from_env(env_name, field=f"headers_env.{name}")
        return headers

    async def _send_with_retries(self, client: HttpClient, request: HttpRequest) -> None:
        last_error: DestinationUnavailableError | None = None
        attempts = self.config.retry.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = await client.send(
                    request,
                    timeout_seconds=self.config.request_timeout_seconds,
                    max_response_bytes=self.config.max_response_bytes,
                )
                self._classify_response(response)
                return
            except DestinationUnavailableError as exc:
                last_error = exc
                if not await self._sleep_before_next_attempt(attempt, attempts):
                    break
            except NatsSinksError:
                raise
            except TimeoutError as exc:
                last_error = DestinationUnavailableError("HTTP sink request timed out")
                last_error.__cause__ = exc
                if not await self._sleep_before_next_attempt(attempt, attempts):
                    break
            except OSError as exc:
                last_error = DestinationUnavailableError("HTTP sink request failed before response")
                last_error.__cause__ = exc
                if not await self._sleep_before_next_attempt(attempt, attempts):
                    break
            except Exception as exc:
                raise DestinationUnavailableError(
                    "HTTP sink client failed before endpoint success was confirmed"
                ) from exc

        if last_error is not None:
            raise last_error
        raise DestinationUnavailableError("HTTP sink request failed without a response")

    async def _sleep_before_next_attempt(self, attempt: int, attempts: int) -> bool:
        """Sleep according to retry policy and return true when another attempt remains."""

        if attempt >= attempts:
            return False
        delay = self._retry_policy.backoff_seconds(attempt)
        if delay > 0:
            await asyncio.sleep(delay)
        return True

    def _classify_response(self, response: HttpResponse) -> None:
        if response.status in self.config.success_statuses:
            return
        if response.status in self.config.retry_statuses:
            raise DestinationUnavailableError(
                f"HTTP sink returned retryable HTTP status {response.status}"
            )
        raise PermanentSinkError(f"HTTP sink returned non-success HTTP status {response.status}")


def _header_value_from_env(name: str, *, field: str) -> str:
    """Resolve an environment-backed header without logging the value."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ConfigurationError(f"{field} environment variable is not set")
    if value != value.strip():
        raise ConfigurationError(f"{field} environment variable must not contain padding")
    if any(
        ord(character) < _CONTROL_CHARACTER_CUTOFF or ord(character) == _ASCII_DELETE
        for character in value
    ):
        raise ConfigurationError(f"{field} environment variable contains control characters")
    return value
