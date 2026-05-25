# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""StatsD observability connector.

StatsD is intentionally treated as a best-effort observability target.  The
connector reads a local nats-sinks metrics snapshot, applies the shared
observability allow/deny policy, renders one bounded datagram per approved
aggregate metric, and sends those datagrams to a configured UDP or Unix datagram
socket target.

The connector never reads NATS messages, destination records, message payloads,
subjects, classification labels, mission metadata, message IDs, file paths, or
table names.  It also never participates in JetStream ACK, NAK, DLQ, retry, or
sink write decisions.
"""

from __future__ import annotations

import math
import re
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.prometheus import filter_metric_rows

DISABLED_STATSD_TEXT = "StatsD export disabled by observability policy\n"
EMPTY_STATSD_TEXT = "StatsD export produced no allowed metrics\n"
STATSD_PROFILE_NAME = "statsd"
STATSD_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class StatsdSocket(Protocol):
    """Small socket protocol used for deterministic StatsD exporter tests."""

    def settimeout(self, value: float) -> None:
        """Set the socket timeout."""

    def sendto(self, data: bytes, address: object) -> int:
        """Send one StatsD datagram."""

    def close(self) -> None:
        """Close the socket."""


SocketFactory = Callable[[int, int], StatsdSocket]


@dataclass(frozen=True, slots=True)
class StatsdExportResult:
    """Safe result summary returned by the StatsD connector.

    The summary deliberately excludes destination addresses and socket paths.
    It can be logged without disclosing deployment-specific observability
    topology.
    """

    attempted: bool
    delivered: bool
    attempts: int
    datagrams: int
    message: str


def ensure_statsd_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless StatsD export is explicitly enabled."""

    if not policy.enabled or not policy.statsd.enabled:
        raise ConfigurationError("StatsD export is disabled by observability policy")
    if policy.statsd.transport == "unixgram" and policy.statsd.socket_path is None:
        raise ConfigurationError("statsd.socket_path is required when statsd.transport is unixgram")


def filter_statsd_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for StatsD export by the shared policy model."""

    return filter_metric_rows(snapshot, policy)


def _format_number(value: float) -> str:
    """Render finite numbers compactly for StatsD datagrams."""

    if not math.isfinite(float(value)):
        raise ValueError("StatsD metric values must be finite")
    rendered = float(value)
    if rendered.is_integer():
        return str(int(rendered))
    return f"{rendered:.12g}"


def _normalize_metric_component(value: str) -> str:
    """Normalize a StatsD name component while keeping it readable."""

    rendered = STATSD_SAFE_NAME_RE.sub("_", value.strip())
    rendered = re.sub(r"_+", "_", rendered).strip("._-")
    if not rendered:
        raise ValueError("StatsD metric name normalized to an empty value")
    return rendered


def _metric_prefix(policy: ObservabilityPolicy) -> str:
    """Return the configured StatsD prefix, falling back to the policy namespace."""

    return _normalize_metric_component(policy.statsd.metric_prefix or policy.namespace)


def statsd_metric_name(row: MetricRow, policy: ObservabilityPolicy) -> str:
    """Return the StatsD metric name for one approved row."""

    return f"{_metric_prefix(policy)}.{_normalize_metric_component(row.name)}"


def _statsd_type(row: MetricRow) -> str:
    """Return the StatsD metric type for a flattened metric row."""

    _ = row
    # Metrics snapshots contain absolute aggregate values.  StatsD counters are
    # normally deltas, so exporting snapshot counters as `|c` would over-count
    # when an observability timer runs repeatedly over the same process.  Gauges
    # preserve the current snapshot value without requiring local delta state.
    return "g"


def render_statsd_lines(snapshot: dict[str, object] | None, policy: ObservabilityPolicy) -> str:
    """Render safe StatsD lines for policy-approved metrics.

    Disabled policies return a harmless explanatory line and do not need a
    snapshot.  Enabled policies require a validated metrics snapshot.  The
    output is designed for dry-run review and deliberately contains no delivery
    subject, payload, classification, labels, mission metadata, destination, or
    credential details.
    """

    if not policy.enabled or not policy.statsd.enabled:
        return DISABLED_STATSD_TEXT
    if snapshot is None:
        raise ValueError("an enabled StatsD policy requires a metrics snapshot")

    rows = filter_statsd_metric_rows(snapshot, policy)
    if not rows:
        return EMPTY_STATSD_TEXT

    lines = [
        f"{statsd_metric_name(row, policy)}:{_format_number(row.value)}|{_statsd_type(row)}"
        for row in rows
    ]
    return "\n".join(lines) + "\n"


def build_statsd_datagrams(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[bytes]:
    """Build bounded StatsD datagrams for the approved metrics."""

    ensure_statsd_enabled(policy)
    rendered = render_statsd_lines(snapshot, policy)
    if rendered == EMPTY_STATSD_TEXT:
        return []
    datagrams = [line.encode("utf-8") for line in rendered.rstrip("\n").splitlines()]
    for datagram in datagrams:
        if len(datagram) > policy.statsd.max_datagram_bytes:
            raise ConfigurationError(
                "StatsD datagram exceeds statsd.max_datagram_bytes; reduce metric_prefix, "
                "reduce the allow list, or increase the bound after review"
            )
    return datagrams


def _statsd_address(policy: ObservabilityPolicy) -> object:
    """Return the socket address without exposing it in result messages."""

    if policy.statsd.transport == "unixgram":
        if policy.statsd.socket_path is None:
            raise ConfigurationError(
                "statsd.socket_path is required when statsd.transport is unixgram"
            )
        return policy.statsd.socket_path
    return (policy.statsd.host, policy.statsd.port)


def _socket_family(policy: ObservabilityPolicy) -> int:
    """Return the socket family for the configured StatsD transport."""

    if policy.statsd.transport == "unixgram":
        return socket.AF_UNIX
    return socket.AF_INET


def _safe_failure_message(exc: BaseException) -> str:
    """Return a sanitized failure category for CLI output and logs."""

    return f"StatsD export failed with {type(exc).__name__}"


def _send_datagrams_once(
    datagrams: Sequence[bytes],
    policy: ObservabilityPolicy,
    *,
    socket_factory: SocketFactory,
) -> None:
    """Send all datagrams once through a short-lived datagram socket."""

    sock = socket_factory(_socket_family(policy), socket.SOCK_DGRAM)
    try:
        sock.settimeout(policy.statsd.timeout_seconds)
        address = _statsd_address(policy)
        for datagram in datagrams:
            sock.sendto(datagram, address)
    finally:
        sock.close()


def export_statsd_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    socket_factory: SocketFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> StatsdExportResult:
    """Export approved metrics to StatsD with bounded retry behavior."""

    if not policy.enabled or not policy.statsd.enabled:
        return StatsdExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            datagrams=0,
            message=DISABLED_STATSD_TEXT.strip(),
        )

    rows = filter_statsd_metric_rows(snapshot, policy)
    if not rows:
        return StatsdExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            datagrams=0,
            message=EMPTY_STATSD_TEXT.strip(),
        )

    datagrams = build_statsd_datagrams(snapshot, policy)
    selected_socket_factory = (
        socket_factory if socket_factory is not None else cast(SocketFactory, socket.socket)
    )
    max_attempts = policy.statsd.max_retries + 1
    last_message = "StatsD export did not run"

    for attempt in range(1, max_attempts + 1):
        try:
            _send_datagrams_once(
                datagrams,
                policy,
                socket_factory=selected_socket_factory,
            )
            return StatsdExportResult(
                attempted=True,
                delivered=True,
                attempts=attempt,
                datagrams=len(datagrams),
                message="StatsD export delivered",
            )
        except (OSError, TimeoutError) as exc:
            last_message = _safe_failure_message(exc)

        if attempt < max_attempts and policy.statsd.retry_backoff_seconds > 0:
            sleep(policy.statsd.retry_backoff_seconds)

    return StatsdExportResult(
        attempted=True,
        delivered=False,
        attempts=max_attempts,
        datagrams=len(datagrams),
        message=last_message,
    )
