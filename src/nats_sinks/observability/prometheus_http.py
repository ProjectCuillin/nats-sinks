# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Native Prometheus scrape endpoint for approved local metrics.

The HTTP endpoint is deliberately implemented as an observability connector
rather than as part of the delivery-critical runner.  It reads the same local
metrics snapshots used by the textfile connector, applies the same allow-list
policy, and serves only the rendered Prometheus exposition text.  It never
connects to NATS, never talks to a destination sink, and never receives raw
messages, so endpoint failures cannot ACK, NAK, retry, or block sink writes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import load_metrics_snapshot
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.prometheus import (
    DISABLED_PROMETHEUS_TEXT,
    render_prometheus_textfile,
)

LOGGER = logging.getLogger(__name__)
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@dataclass(frozen=True, slots=True)
class PrometheusHttpResponse:
    """Small immutable HTTP response used by tests, CLI dry-runs, and handlers."""

    status_code: int
    body: bytes
    content_type: str = PROMETHEUS_CONTENT_TYPE


def _snapshot_age_seconds(snapshot: dict[str, object], *, now: float | None = None) -> float:
    """Return snapshot age while rejecting malformed snapshot timestamps."""

    generated = snapshot.get("generated_at_epoch_seconds")
    if not isinstance(generated, int | float) or isinstance(generated, bool):
        raise ValueError("metrics snapshot generated_at_epoch_seconds must be numeric")
    current = time.time() if now is None else now
    return max(current - float(generated), 0.0)


def _endpoint_enabled(policy: ObservabilityPolicy) -> bool:
    """Return whether the native HTTP endpoint is explicitly enabled."""

    return policy.enabled and policy.prometheus.http_endpoint.enabled


def ensure_prometheus_http_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless both the global policy and HTTP endpoint are enabled."""

    if not _endpoint_enabled(policy):
        raise ConfigurationError(
            "Prometheus HTTP endpoint is disabled by observability policy. "
            "Set both policy.enabled and policy.prometheus.http_endpoint.enabled to true."
        )


def _render_enabled_response(
    snapshot_file: str | Path,
    policy: ObservabilityPolicy,
    *,
    allow_stale: bool,
    now: float | None,
) -> PrometheusHttpResponse:
    """Load a snapshot and render policy-approved Prometheus exposition text."""

    try:
        snapshot = load_metrics_snapshot(snapshot_file)
        stale_after = policy.prometheus.stale_after_seconds
        if stale_after is not None:
            age = _snapshot_age_seconds(snapshot, now=now)
            if age > stale_after and not allow_stale:
                return PrometheusHttpResponse(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    b"# nats-sinks metrics snapshot is stale\n",
                )
        exposition_policy = policy.model_copy(
            update={
                "prometheus": policy.prometheus.model_copy(
                    update={"enabled": True},
                    deep=True,
                )
            },
            deep=True,
        )
        text = render_prometheus_textfile(snapshot, exposition_policy)
        body = text.encode("utf-8")
        if len(body) > policy.prometheus.http_endpoint.response_max_bytes:
            return PrometheusHttpResponse(
                HTTPStatus.SERVICE_UNAVAILABLE,
                b"# nats-sinks Prometheus response suppressed by response_max_bytes policy\n",
            )
        return PrometheusHttpResponse(HTTPStatus.OK, body)
    except (OSError, ValueError, ConfigurationError):
        LOGGER.exception("Prometheus HTTP endpoint could not render metrics safely")
        return PrometheusHttpResponse(
            HTTPStatus.SERVICE_UNAVAILABLE,
            b"# nats-sinks Prometheus endpoint could not render metrics safely\n",
        )


def render_prometheus_http_response(
    snapshot_file: str | Path,
    policy: ObservabilityPolicy,
    *,
    request_path: str = "/metrics",
    allow_stale: bool = False,
    now: float | None = None,
) -> PrometheusHttpResponse:
    """Render one HTTP response without opening a socket.

    This pure entry point keeps endpoint behavior testable without making unit
    tests perform network calls.  The live server uses the same function for
    actual requests.
    """

    endpoint = policy.prometheus.http_endpoint
    parsed_path = urlsplit(request_path).path or "/"
    if parsed_path != endpoint.path:
        return PrometheusHttpResponse(
            HTTPStatus.NOT_FOUND,
            b"# nats-sinks Prometheus endpoint not found\n",
        )
    if not _endpoint_enabled(policy):
        return PrometheusHttpResponse(
            HTTPStatus.NOT_FOUND,
            DISABLED_PROMETHEUS_TEXT.encode("utf-8"),
        )
    return _render_enabled_response(
        snapshot_file,
        policy,
        allow_stale=allow_stale,
        now=now,
    )


def _handler_class(
    *,
    snapshot_file: str | Path,
    policy: ObservabilityPolicy,
    allow_stale: bool,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to one snapshot and policy."""

    snapshot_path = Path(snapshot_file)

    class PrometheusRequestHandler(BaseHTTPRequestHandler):
        """Serve one policy-filtered Prometheus endpoint."""

        server_version = "nats-sinks-prometheus"
        protocol_version = "HTTP/1.1"
        _snapshot_file: ClassVar[Path] = snapshot_path
        _policy: ClassVar[ObservabilityPolicy] = policy
        _allow_stale: ClassVar[bool] = allow_stale

        def do_GET(self) -> None:
            """Serve the configured metrics path or a small not-found response."""

            response = render_prometheus_http_response(
                self._snapshot_file,
                self._policy,
                request_path=self.path,
                allow_stale=self._allow_stale,
            )
            self.send_response(int(response.status_code))
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Suppress default access logs to avoid leaking request details."""

            del format, args

    return PrometheusRequestHandler


def build_prometheus_http_server(
    snapshot_file: str | Path,
    policy: ObservabilityPolicy,
    *,
    allow_stale: bool = False,
) -> ThreadingHTTPServer:
    """Build a configured HTTP server without starting `serve_forever`."""

    ensure_prometheus_http_enabled(policy)
    endpoint = policy.prometheus.http_endpoint
    handler = _handler_class(
        snapshot_file=snapshot_file,
        policy=policy,
        allow_stale=allow_stale,
    )
    server = ThreadingHTTPServer((endpoint.host, endpoint.port), handler)
    server.timeout = endpoint.request_timeout_seconds
    return server


def serve_prometheus_http(
    snapshot_file: str | Path,
    policy: ObservabilityPolicy,
    *,
    allow_stale: bool = False,
) -> None:
    """Run the native Prometheus endpoint until interrupted.

    The server is intended for a separate observability service.  It should not
    run inside the delivery-critical sink worker unless an embedding
    application explicitly accepts that operational coupling.
    """

    server = build_prometheus_http_server(snapshot_file, policy, allow_stale=allow_stale)
    endpoint = policy.prometheus.http_endpoint
    try:
        LOGGER.info(
            "starting nats-sinks Prometheus HTTP endpoint on %s:%s%s",
            endpoint.host,
            endpoint.port,
            endpoint.path,
        )
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOGGER.info("stopping nats-sinks Prometheus HTTP endpoint after interrupt")
    finally:
        server.server_close()
