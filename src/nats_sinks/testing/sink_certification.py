# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Reusable sink certification helpers for maintainers.

The public sink protocol is deliberately small, but production readiness
requires more evidence than "the object has a ``write_batch`` method".  A sink
must start cleanly, accept normalized ``NatsEnvelope`` objects, return success
only after the destination's durable boundary has been crossed, classify
failures through framework errors, preserve duplicate-redelivery behavior, and
avoid any direct JetStream acknowledgement capability.

This module provides small, deterministic helper functions that sink-specific
unit tests can reuse.  They intentionally do not connect to NATS, Oracle, file
systems other than test-managed temporary directories, cloud services, or any
other live destination.  Destination-specific tests remain responsible for
asserting the exact durable side effect, such as "the file exists" or "the
transaction committed".
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeAlias

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.sinks.base import Sink

ACK_PRIMITIVE_NAMES = frozenset(
    {
        "ack",
        "nak",
        "term",
        "in_progress",
        "ack_sync",
        "nak_sync",
        "term_sync",
    }
)

SinkAssertion: TypeAlias = Callable[[Sink], None | Awaitable[None]]
BatchAssertion: TypeAlias = Callable[[Sink, Sequence[NatsEnvelope]], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SinkCertificationCase:
    """Describes one reusable sink certification scenario.

    ``sink_factory`` must create a fresh sink instance for each test run so
    certification helpers do not accidentally share mutable state between
    cases.  ``messages`` should be synthetic and non-sensitive.  If
    ``duplicate_messages`` is omitted, duplicate-redelivery certification is
    skipped for that case.
    """

    name: str
    sink_factory: Callable[[], Sink]
    messages: tuple[NatsEnvelope, ...]
    duplicate_messages: tuple[NatsEnvelope, ...] = field(default_factory=tuple)
    after_write: BatchAssertion | None = None
    after_duplicate_write: BatchAssertion | None = None

    def __post_init__(self) -> None:
        """Reject incomplete certification cases early in unit tests."""

        if not self.name.strip():
            raise ValueError("sink certification case name must not be empty")
        if not self.messages:
            raise ValueError("sink certification case must include at least one message")


def certification_envelope(
    *,
    subject: str = "certification.events.created",
    data: bytes = b'{"event_id":"CERT-1","status":"ok"}',
    stream: str = "CERTIFICATION",
    stream_sequence: int = 1,
    message_id: str | None = "certification-message-1",
    priority: str | None = "normal",
    classification: str | None = "unclassified",
    labels: Sequence[str] = ("certification",),
) -> NatsEnvelope:
    """Build a deterministic non-sensitive envelope for sink tests.

    The helper mirrors the metadata shape that production sinks see from the
    core runtime while avoiding any real operational subject, payload,
    credential, host, or tenant value.  Tests can override individual fields
    when they need to exercise duplicate handling, payload wrapping, or
    destination-specific routing.
    """

    return NatsEnvelope(
        subject=subject,
        data=data,
        headers={"Nats-Msg-Id": message_id} if message_id else {},
        stream=stream,
        consumer="sink-certification",
        stream_sequence=stream_sequence,
        consumer_sequence=stream_sequence,
        timestamp=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        message_id=message_id,
        redelivered=False,
        pending=0,
        priority=priority,
        classification=classification,
        labels=tuple(labels),
    )


def assert_envelope_has_no_ack_primitives(envelope: NatsEnvelope) -> None:
    """Assert that a sink-bound envelope cannot acknowledge JetStream messages."""

    exposed = sorted(name for name in ACK_PRIMITIVE_NAMES if hasattr(envelope, name))
    if exposed:
        joined = ", ".join(exposed)
        raise AssertionError(f"NatsEnvelope exposes JetStream ACK primitive(s): {joined}")


def assert_sink_protocol_boundary(sink: Sink) -> None:
    """Assert that a sink instance satisfies the public protocol boundary.

    Sinks may have destination-specific private helpers, but the public
    contract expected by the core is ``start``, ``write_batch``, and ``stop``.
    This helper also verifies that those methods are awaitable functions so
    they can be safely used by the async runner.
    """

    if not isinstance(sink, Sink):
        raise AssertionError(f"{type(sink).__name__} does not satisfy the Sink protocol")
    for method_name in ("start", "write_batch", "stop"):
        method = getattr(sink, method_name, None)
        if not callable(method):
            raise AssertionError(f"{type(sink).__name__}.{method_name} is not callable")
        if not inspect.iscoroutinefunction(method):
            raise AssertionError(f"{type(sink).__name__}.{method_name} must be an async function")


async def certify_sink_lifecycle(case: SinkCertificationCase) -> Sink:
    """Start and stop a fresh sink instance.

    The returned sink has already been stopped.  Tests normally use this helper
    to prove basic lifecycle compatibility; durable side-effect checks should
    use ``certify_sink_write_success``.
    """

    sink = case.sink_factory()
    assert_sink_protocol_boundary(sink)
    await sink.start()
    await sink.stop()
    return sink


async def certify_sink_write_success(case: SinkCertificationCase) -> Sink:
    """Run a basic write certification scenario for one sink.

    The helper starts a fresh sink, verifies that every envelope lacks ACK
    primitives, writes the configured batch, then invokes the destination-
    specific ``after_write`` assertion if one was supplied.  ``stop`` is called
    in a ``finally`` block so failures do not leak resources in tests.
    """

    sink = case.sink_factory()
    assert_sink_protocol_boundary(sink)
    await sink.start()
    try:
        for message in case.messages:
            assert_envelope_has_no_ack_primitives(message)
        await sink.write_batch(case.messages)
        await _maybe_await(case.after_write, sink, case.messages)
    finally:
        await sink.stop()
    return sink


async def certify_sink_duplicate_redelivery(case: SinkCertificationCase) -> Sink:
    """Run a duplicate-redelivery certification scenario for one sink.

    Duplicate certification is optional because some sinks may use external
    idempotency services or live destinations that are not suitable for unit
    tests.  When ``duplicate_messages`` is present, the helper writes the same
    batch twice and lets the destination-specific assertion prove the expected
    idempotent outcome.
    """

    if not case.duplicate_messages:
        raise ValueError("duplicate certification requires duplicate_messages")

    sink = case.sink_factory()
    assert_sink_protocol_boundary(sink)
    await sink.start()
    try:
        for message in case.duplicate_messages:
            assert_envelope_has_no_ack_primitives(message)
        await sink.write_batch(case.duplicate_messages)
        await sink.write_batch(case.duplicate_messages)
        await _maybe_await(case.after_duplicate_write, sink, case.duplicate_messages)
    finally:
        await sink.stop()
    return sink


def assert_log_records_exclude_sensitive_values(
    records: Sequence[Any],
    *,
    sensitive_values: Sequence[str],
) -> None:
    """Assert that log records do not contain known sensitive test markers.

    The helper intentionally searches rendered messages and exception text
    because both often end up in CI logs.  Tests should pass artificial marker
    strings rather than real secrets.
    """

    needles = tuple(value for value in sensitive_values if value)
    if not needles:
        return

    for record in records:
        rendered = _safe_record_text(record)
        for value in needles:
            if value in rendered:
                raise AssertionError(
                    f"log record contains sensitive certification marker {value!r}"
                )


async def _maybe_await(
    callback: BatchAssertion | None,
    sink: Sink,
    messages: Sequence[NatsEnvelope],
) -> None:
    if callback is None:
        return
    result = callback(sink, messages)
    if inspect.isawaitable(result):
        await result


def _safe_record_text(record: Any) -> str:
    try:
        message = record.getMessage()
    except Exception:
        message = ""
    try:
        exc_text = "" if record.exc_info is None else repr(record.exc_info)
    except Exception:
        exc_text = ""
    return f"{message}\n{exc_text}"
