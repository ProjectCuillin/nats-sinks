# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Syslog observability bridge.

The syslog bridge exports only policy-approved aggregate metrics from a local
metrics snapshot.  It renders RFC 5424-style structured messages and sends them
through a configured UDP or Unix datagram transport.  The bridge belongs to the
observability plane: it never reads NATS messages, message payloads, Oracle
rows, file output, subjects, classification values, labels, mission metadata,
or destination configuration, and it never participates in ACK, NAK, DLQ,
retry, idempotency, or sink-write decisions.
"""

from __future__ import annotations

import math
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricRow
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.prometheus import filter_metric_rows

DISABLED_SYSLOG_TEXT = "Syslog export disabled by observability policy\n"
EMPTY_SYSLOG_TEXT = "Syslog export produced no allowed metrics\n"
SYSLOG_PROFILE_NAME = "syslog"
ASCII_CONTROL_MAX_CODEPOINT = 31
ASCII_DELETE_CODEPOINT = 127

SYSLOG_FACILITY_CODES = {
    "kern": 0,
    "user": 1,
    "mail": 2,
    "daemon": 3,
    "auth": 4,
    "syslog": 5,
    "lpr": 6,
    "news": 7,
    "uucp": 8,
    "cron": 9,
    "authpriv": 10,
    "ftp": 11,
    "ntp": 12,
    "audit": 13,
    "alert": 14,
    "clock": 15,
    "local0": 16,
    "local1": 17,
    "local2": 18,
    "local3": 19,
    "local4": 20,
    "local5": 21,
    "local6": 22,
    "local7": 23,
}
SYSLOG_SEVERITY_CODES = {
    "emergency": 0,
    "alert": 1,
    "critical": 2,
    "error": 3,
    "warning": 4,
    "notice": 5,
    "info": 6,
    "debug": 7,
}


class SyslogSocket(Protocol):
    """Small socket protocol used for deterministic syslog bridge tests."""

    def settimeout(self, value: float) -> None:
        """Set the socket timeout."""

    def sendto(self, data: bytes, address: object) -> int:
        """Send one syslog datagram."""

    def close(self) -> None:
        """Close the socket."""


SocketFactory = Callable[[int, int], SyslogSocket]


@dataclass(frozen=True, slots=True)
class SyslogExportResult:
    """Safe result summary returned by the syslog bridge.

    The summary deliberately excludes destination addresses and socket paths.
    It can be logged without disclosing deployment-specific observability
    topology.
    """

    attempted: bool
    delivered: bool
    attempts: int
    messages: int
    message: str


def ensure_syslog_enabled(policy: ObservabilityPolicy) -> None:
    """Fail closed unless syslog export is explicitly enabled."""

    if not policy.enabled or not policy.syslog.enabled:
        raise ConfigurationError("Syslog export is disabled by observability policy")
    if policy.syslog.transport == "unixgram" and policy.syslog.socket_path is None:
        raise ConfigurationError("syslog.socket_path is required when syslog.transport is unixgram")


def filter_syslog_metric_rows(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[MetricRow]:
    """Return metrics allowed for syslog export by the shared policy model."""

    return filter_metric_rows(snapshot, policy)


def _snapshot_time(snapshot: dict[str, object]) -> str:
    """Return an RFC 5424 timestamp based on the metrics snapshot time."""

    generated = snapshot.get("generated_at_epoch_seconds")
    if not isinstance(generated, int | float) or not math.isfinite(float(generated)):
        raise ValueError("metrics snapshot generated_at_epoch_seconds must be finite numeric")
    rendered = float(generated)
    if rendered < 0:
        raise ValueError("metrics snapshot generated_at_epoch_seconds must not be negative")
    return (
        datetime.fromtimestamp(rendered, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _format_number(value: float) -> str:
    """Render finite numbers compactly for syslog structured data."""

    if not math.isfinite(float(value)):
        raise ValueError("Syslog metric values must be finite")
    rendered = float(value)
    if rendered.is_integer():
        return str(int(rendered))
    return f"{rendered:.12g}"


def _priority(policy: ObservabilityPolicy) -> int:
    """Return the RFC 5424 PRI value from the configured facility and severity."""

    return (
        SYSLOG_FACILITY_CODES[policy.syslog.facility] * 8
        + SYSLOG_SEVERITY_CODES[policy.syslog.severity]
    )


def _escape_structured_data_value(value: str) -> str:
    """Escape an RFC 5424 structured-data parameter value."""

    parts: list[str] = []
    for character in value:
        if character in {'"', "\\", "]"}:
            parts.append(f"\\{character}")
        elif ord(character) <= ASCII_CONTROL_MAX_CODEPOINT or (
            ord(character) == ASCII_DELETE_CODEPOINT
        ):
            parts.append("?")
        else:
            parts.append(character)
    return "".join(parts)


def build_syslog_message(
    row: MetricRow, snapshot: dict[str, object], policy: ObservabilityPolicy
) -> str:
    """Build one RFC 5424-style syslog message for one approved metric row."""

    ensure_syslog_enabled(policy)
    params = {
        "metric": row.name,
        "kind": row.kind,
        "value": _format_number(row.value),
        "namespace": policy.namespace,
        "profile": SYSLOG_PROFILE_NAME,
    }
    structured_params = " ".join(
        f'{key}="{_escape_structured_data_value(value)}"' for key, value in params.items()
    )
    structured_data = f"[{policy.syslog.structured_data_id} {structured_params}]"
    return (
        f"<{_priority(policy)}>1 {_snapshot_time(snapshot)} {policy.syslog.hostname} "
        f"{policy.syslog.app_name} {policy.syslog.procid} {policy.syslog.msgid} "
        f"{structured_data} -"
    )


def render_syslog_messages(
    snapshot: dict[str, object] | None,
    policy: ObservabilityPolicy,
) -> str:
    """Render safe syslog messages for policy-approved metrics.

    Disabled policies return a harmless explanatory line and do not need a
    snapshot.  Enabled policies require a validated metrics snapshot.  The
    output is suitable for dry-run review and deliberately contains no delivery
    subject, payload, classification, labels, mission metadata, destination, or
    credential details.
    """

    if not policy.enabled or not policy.syslog.enabled:
        return DISABLED_SYSLOG_TEXT
    if snapshot is None:
        raise ValueError("an enabled syslog policy requires a metrics snapshot")

    rows = filter_syslog_metric_rows(snapshot, policy)
    if not rows:
        return EMPTY_SYSLOG_TEXT

    return "\n".join(build_syslog_message(row, snapshot, policy) for row in rows) + "\n"


def build_syslog_datagrams(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
) -> list[bytes]:
    """Build bounded syslog datagrams for the approved metrics."""

    ensure_syslog_enabled(policy)
    rendered = render_syslog_messages(snapshot, policy)
    if rendered == EMPTY_SYSLOG_TEXT:
        return []
    datagrams = [line.encode("utf-8") for line in rendered.rstrip("\n").splitlines()]
    for datagram in datagrams:
        if len(datagram) > policy.syslog.max_message_bytes:
            raise ConfigurationError(
                "Syslog message exceeds syslog.max_message_bytes; reduce the allow list, "
                "use shorter syslog header fields, or increase the bound after review"
            )
    return datagrams


def _syslog_address(policy: ObservabilityPolicy) -> object:
    """Return the socket address without exposing it in result messages."""

    if policy.syslog.transport == "unixgram":
        if policy.syslog.socket_path is None:
            raise ConfigurationError(
                "syslog.socket_path is required when syslog.transport is unixgram"
            )
        return policy.syslog.socket_path
    return (policy.syslog.host, policy.syslog.port)


def _socket_family(policy: ObservabilityPolicy) -> int:
    """Return the socket family for the configured syslog transport."""

    if policy.syslog.transport == "unixgram":
        return socket.AF_UNIX
    return socket.AF_INET


def _safe_failure_message(exc: BaseException) -> str:
    """Return a sanitized failure category for CLI output and logs."""

    return f"Syslog export failed with {type(exc).__name__}"


def _send_datagrams_once(
    datagrams: Sequence[bytes],
    policy: ObservabilityPolicy,
    *,
    socket_factory: SocketFactory,
) -> None:
    """Send all datagrams once through a short-lived datagram socket."""

    sock = socket_factory(_socket_family(policy), socket.SOCK_DGRAM)
    try:
        sock.settimeout(policy.syslog.timeout_seconds)
        address = _syslog_address(policy)
        for datagram in datagrams:
            sock.sendto(datagram, address)
    finally:
        sock.close()


def export_syslog_metrics(
    snapshot: dict[str, object],
    policy: ObservabilityPolicy,
    *,
    socket_factory: SocketFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SyslogExportResult:
    """Export approved metrics to syslog with bounded retry behavior."""

    if not policy.enabled or not policy.syslog.enabled:
        return SyslogExportResult(
            attempted=False,
            delivered=False,
            attempts=0,
            messages=0,
            message=DISABLED_SYSLOG_TEXT.strip(),
        )

    rows = filter_syslog_metric_rows(snapshot, policy)
    if not rows:
        return SyslogExportResult(
            attempted=False,
            delivered=True,
            attempts=0,
            messages=0,
            message=EMPTY_SYSLOG_TEXT.strip(),
        )

    datagrams = build_syslog_datagrams(snapshot, policy)
    selected_socket_factory = (
        socket_factory if socket_factory is not None else cast(SocketFactory, socket.socket)
    )
    max_attempts = policy.syslog.max_retries + 1
    last_message = "Syslog export did not run"

    for attempt in range(1, max_attempts + 1):
        try:
            _send_datagrams_once(
                datagrams,
                policy,
                socket_factory=selected_socket_factory,
            )
            return SyslogExportResult(
                attempted=True,
                delivered=True,
                attempts=attempt,
                messages=len(datagrams),
                message="Syslog export delivered",
            )
        except (OSError, TimeoutError) as exc:
            last_message = _safe_failure_message(exc)

        if attempt < max_attempts and policy.syslog.retry_backoff_seconds > 0:
            sleep(policy.syslog.retry_backoff_seconds)

    return SyslogExportResult(
        attempted=True,
        delivered=False,
        attempts=max_attempts,
        messages=len(datagrams),
        message=last_message,
    )
