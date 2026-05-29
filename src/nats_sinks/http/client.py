# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""HTTP client boundary used by the first-party HTTP sink.

The production client uses Python's standard-library `http.client` module and
does not follow redirects.  Redirect following is intentionally left out so a
configured endpoint cannot silently move traffic to another host after startup
validation.
"""

from __future__ import annotations

import asyncio
import http.client
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from nats_sinks.core.errors import DestinationUnavailableError


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """One prepared HTTP request."""

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Bounded HTTP response metadata."""

    status: int
    body: bytes


class HttpClient(Protocol):
    """Protocol implemented by real and fake HTTP clients."""

    async def send(
        self,
        request: HttpRequest,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        """Send one request and return a bounded response."""


class StandardHttpClient:
    """Small standard-library HTTP client with bounded responses."""

    async def send(
        self,
        request: HttpRequest,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        """Send one request in a worker thread."""

        return await asyncio.to_thread(
            self._send_sync,
            request,
            timeout_seconds,
            max_response_bytes,
        )

    def _send_sync(
        self,
        request: HttpRequest,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        parsed = urlsplit(request.url)
        host = parsed.hostname
        if host is None:
            raise DestinationUnavailableError("HTTP sink URL has no host")
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        connection: http.client.HTTPConnection | http.client.HTTPSConnection
        if parsed.scheme == "https":
            connection = http.client.HTTPSConnection(
                host,
                port=parsed.port,
                timeout=timeout_seconds,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(
                host,
                port=parsed.port,
                timeout=timeout_seconds,
            )
        try:
            connection.request(
                request.method,
                path,
                body=request.body,
                headers=dict(request.headers),
            )
            response = connection.getresponse()
            body = response.read(max_response_bytes + 1)
        except TimeoutError as exc:
            raise DestinationUnavailableError("HTTP sink request timed out") from exc
        except OSError as exc:
            raise DestinationUnavailableError("HTTP sink request failed before response") from exc
        finally:
            connection.close()

        if len(body) > max_response_bytes:
            raise DestinationUnavailableError("HTTP sink response exceeded size limit")
        return HttpResponse(status=int(response.status), body=body)
