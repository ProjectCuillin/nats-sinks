# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""JetStream advisory observation helpers.

JetStream advisories are operational NATS messages emitted by the NATS server
under ``$JS.EVENT.ADVISORY.>``.  They can tell operators that a consumer has
reached maximum delivery attempts, that messages were NAKed or terminally
acknowledged, or that stream/consumer leadership changed in a clustered
deployment.

This module treats advisories as observational data only.  Advisory processing
must never ACK source messages, must never alter retry or DLQ behavior, and
must never inspect sink payloads.  The implementation therefore emits only
low-cardinality counters and optional sanitized log lines.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass
from typing import Protocol, cast

from nats_sinks.core.errors import ValidationError
from nats_sinks.core.metrics import MetricNames, MetricsRecorder, increment_metric
from nats_sinks.core.subjects import matches_subject, validate_subject_pattern

LOGGER = logging.getLogger(__name__)

ADVISORY_SUBJECT_PREFIX = "$JS.EVENT.ADVISORY."
DEFAULT_ADVISORY_MAX_PAYLOAD_BYTES = 65_536
MAX_ADVISORY_PAYLOAD_BYTES = 1_048_576
MAX_ADVISORY_SUBJECTS = 32
MAX_ADVISORY_SUBJECT_LENGTH = 512

ADVISORY_TYPE_PREFIX = "io.nats.jetstream.advisory.v1."
ADVISORY_KIND_UNSUPPORTED = "unsupported"

ADVISORY_KIND_BY_TYPE: dict[str, str] = {
    f"{ADVISORY_TYPE_PREFIX}api_audit": "api_audit",
    f"{ADVISORY_TYPE_PREFIX}consumer_action": "consumer_action",
    f"{ADVISORY_TYPE_PREFIX}consumer_leader_elected": "consumer_leader_elected",
    f"{ADVISORY_TYPE_PREFIX}consumer_quorum_lost": "consumer_quorum_lost",
    f"{ADVISORY_TYPE_PREFIX}max_deliver": "max_deliver",
    f"{ADVISORY_TYPE_PREFIX}nak": "nak",
    f"{ADVISORY_TYPE_PREFIX}stream_action": "stream_action",
    f"{ADVISORY_TYPE_PREFIX}stream_leader_elected": "stream_leader_elected",
    f"{ADVISORY_TYPE_PREFIX}stream_quorum_lost": "stream_quorum_lost",
    f"{ADVISORY_TYPE_PREFIX}terminated": "terminated",
}

ADVISORY_KIND_BY_SUBJECT_TOKEN: tuple[tuple[str, str], ...] = (
    ("$JS.EVENT.ADVISORY.API", "api_audit"),
    ("$JS.EVENT.ADVISORY.STREAM.CREATED.", "stream_action"),
    ("$JS.EVENT.ADVISORY.STREAM.DELETED.", "stream_action"),
    ("$JS.EVENT.ADVISORY.STREAM.MODIFIED.", "stream_action"),
    ("$JS.EVENT.ADVISORY.STREAM.LEADER_ELECTED.", "stream_leader_elected"),
    ("$JS.EVENT.ADVISORY.STREAM.QUORUM_LOST.", "stream_quorum_lost"),
    ("$JS.EVENT.ADVISORY.CONSUMER.CREATED.", "consumer_action"),
    ("$JS.EVENT.ADVISORY.CONSUMER.DELETED.", "consumer_action"),
    ("$JS.EVENT.ADVISORY.CONSUMER.MODIFIED.", "consumer_action"),
    ("$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.", "max_deliver"),
    ("$JS.EVENT.ADVISORY.CONSUMER.MSG_NAKED.", "nak"),
    ("$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.", "terminated"),
    ("$JS.EVENT.ADVISORY.CONSUMER.LEADER_ELECTED.", "consumer_leader_elected"),
    ("$JS.EVENT.ADVISORY.CONSUMER.QUORUM_LOST.", "consumer_quorum_lost"),
)

ADVISORY_METRIC_BY_KIND: dict[str, str] = {
    "api_audit": MetricNames.JETSTREAM_ADVISORY_API_AUDIT_TOTAL,
    "consumer_action": MetricNames.JETSTREAM_ADVISORY_CONSUMER_ACTION_TOTAL,
    "consumer_leader_elected": MetricNames.JETSTREAM_ADVISORY_CONSUMER_LEADER_ELECTED_TOTAL,
    "consumer_quorum_lost": MetricNames.JETSTREAM_ADVISORY_CONSUMER_QUORUM_LOST_TOTAL,
    "max_deliver": MetricNames.JETSTREAM_ADVISORY_MAX_DELIVER_TOTAL,
    "nak": MetricNames.JETSTREAM_ADVISORY_NAK_TOTAL,
    "stream_action": MetricNames.JETSTREAM_ADVISORY_STREAM_ACTION_TOTAL,
    "stream_leader_elected": MetricNames.JETSTREAM_ADVISORY_STREAM_LEADER_ELECTED_TOTAL,
    "stream_quorum_lost": MetricNames.JETSTREAM_ADVISORY_STREAM_QUORUM_LOST_TOTAL,
    "terminated": MetricNames.JETSTREAM_ADVISORY_TERMINATED_TOTAL,
}

DEFAULT_ADVISORY_SUBJECTS: tuple[str, ...] = (
    "$JS.EVENT.ADVISORY.API",
    "$JS.EVENT.ADVISORY.API.>",
    "$JS.EVENT.ADVISORY.STREAM.CREATED.*",
    "$JS.EVENT.ADVISORY.STREAM.DELETED.*",
    "$JS.EVENT.ADVISORY.STREAM.MODIFIED.*",
    "$JS.EVENT.ADVISORY.STREAM.LEADER_ELECTED.*",
    "$JS.EVENT.ADVISORY.STREAM.QUORUM_LOST.*",
    "$JS.EVENT.ADVISORY.CONSUMER.CREATED.*.*",
    "$JS.EVENT.ADVISORY.CONSUMER.DELETED.*.*",
    "$JS.EVENT.ADVISORY.CONSUMER.MODIFIED.*.*",
    "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",
    "$JS.EVENT.ADVISORY.CONSUMER.MSG_NAKED.*.*",
    "$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.*.*",
    "$JS.EVENT.ADVISORY.CONSUMER.LEADER_ELECTED.*.*",
    "$JS.EVENT.ADVISORY.CONSUMER.QUORUM_LOST.*.*",
)


class AdvisoryConfigProtocol(Protocol):
    """Small runtime shape used to avoid coupling this module to Pydantic."""

    enabled: bool
    subjects: tuple[str, ...]
    max_payload_bytes: int
    log_events: bool


@dataclass(frozen=True, slots=True)
class JetStreamAdvisory:
    """Sanitized advisory summary used internally and by tests.

    The object deliberately excludes stream names, consumer names, subjects
    beyond the original subject string, sequence numbers, and payload bodies
    from metrics.  Operators who need full advisory bodies should consume them
    with dedicated, access-controlled NATS tooling instead of the sink worker.
    """

    subject: str
    kind: str
    advisory_type: str | None


async def _maybe_await(value: object) -> None:
    """Await optional async return values from NATS subscription methods."""

    if inspect.isawaitable(value):
        await value


def validate_advisory_subject(value: object) -> str:
    """Validate an advisory subscription subject for configuration loading."""

    if not isinstance(value, str):
        raise ValidationError(f"invalid JetStream advisory subject {value!r}")
    rendered = value.strip()
    if rendered != value or not rendered:
        raise ValidationError("JetStream advisory subject must not be empty or padded")
    if len(rendered) > MAX_ADVISORY_SUBJECT_LENGTH:
        raise ValidationError(
            f"JetStream advisory subject exceeds {MAX_ADVISORY_SUBJECT_LENGTH} characters"
        )
    if "\x00" in rendered or "\n" in rendered or "\r" in rendered:
        raise ValidationError("JetStream advisory subject must not contain control characters")
    if not rendered.startswith(ADVISORY_SUBJECT_PREFIX):
        raise ValidationError(
            f"JetStream advisory subject must start with {ADVISORY_SUBJECT_PREFIX!r}"
        )
    try:
        return validate_subject_pattern(rendered)
    except Exception as exc:
        raise ValidationError(str(exc)) from exc


def advisory_kind_from_subject(subject: str) -> str:
    """Return a low-cardinality advisory kind inferred from a NATS subject."""

    for subject_prefix, kind in ADVISORY_KIND_BY_SUBJECT_TOKEN:
        if subject == subject_prefix or subject.startswith(subject_prefix):
            return kind
    return ADVISORY_KIND_UNSUPPORTED


def _reject_duplicate_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate advisory JSON keys before classification."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate advisory JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject NaN and Infinity in advisory JSON payloads."""

    raise ValidationError(f"non-standard advisory JSON constant is not allowed: {value}")


def parse_jetstream_advisory(
    *,
    subject: str,
    data: bytes,
    max_payload_bytes: int = DEFAULT_ADVISORY_MAX_PAYLOAD_BYTES,
) -> JetStreamAdvisory:
    """Parse and classify a JetStream advisory without exposing payload values."""

    if not isinstance(subject, str) or not subject:
        raise ValidationError("JetStream advisory subject must be a non-empty string")
    if len(data) > max_payload_bytes:
        raise ValidationError(f"JetStream advisory payload exceeds {max_payload_bytes} byte limit")
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object_pairs,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValidationError("JetStream advisory payload must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError("JetStream advisory payload must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("JetStream advisory payload root must be a JSON object")

    advisory_type_value = payload.get("type")
    advisory_type = advisory_type_value if isinstance(advisory_type_value, str) else None
    kind = (
        ADVISORY_KIND_BY_TYPE.get(advisory_type, ADVISORY_KIND_UNSUPPORTED)
        if advisory_type is not None
        else ADVISORY_KIND_UNSUPPORTED
    )
    if kind == ADVISORY_KIND_UNSUPPORTED:
        kind = advisory_kind_from_subject(subject)

    return JetStreamAdvisory(subject=subject, kind=kind, advisory_type=advisory_type)


def observe_jetstream_advisory_message(
    message: object,
    *,
    config: AdvisoryConfigProtocol,
    metrics: MetricsRecorder,
) -> JetStreamAdvisory | None:
    """Process one advisory message as an observation-only metrics event."""

    if not config.enabled:
        return None

    subject = getattr(message, "subject", None)
    if not isinstance(subject, str) or not any(
        matches_subject(pattern, subject) for pattern in config.subjects
    ):
        increment_metric(metrics, MetricNames.JETSTREAM_ADVISORIES_FILTERED_TOTAL)
        return None

    increment_metric(metrics, MetricNames.JETSTREAM_ADVISORIES_RECEIVED_TOTAL)
    data = getattr(message, "data", b"")
    if isinstance(data, bytearray | memoryview):
        data = bytes(data)
    elif not isinstance(data, bytes):
        increment_metric(metrics, MetricNames.JETSTREAM_ADVISORY_PARSE_ERRORS_TOTAL)
        LOGGER.warning("JetStream advisory payload was not bytes-like")
        return None

    try:
        advisory = parse_jetstream_advisory(
            subject=subject,
            data=data,
            max_payload_bytes=config.max_payload_bytes,
        )
    except ValidationError as exc:
        increment_metric(metrics, MetricNames.JETSTREAM_ADVISORY_PARSE_ERRORS_TOTAL)
        LOGGER.warning("JetStream advisory could not be parsed safely: %s", exc)
        return None

    metric_name = ADVISORY_METRIC_BY_KIND.get(advisory.kind)
    if metric_name is None:
        increment_metric(metrics, MetricNames.JETSTREAM_ADVISORY_UNSUPPORTED_TOTAL)
    else:
        increment_metric(metrics, metric_name)

    if config.log_events:
        LOGGER.info("JetStream advisory observed: kind=%s", advisory.kind)
    return advisory


class JetStreamAdvisoryMonitor:
    """Subscribe to selected JetStream advisory subjects using Core NATS."""

    def __init__(
        self,
        nats_connection: object,
        *,
        config: AdvisoryConfigProtocol,
        metrics: MetricsRecorder,
    ) -> None:
        self.nats_connection = nats_connection
        self.config = config
        self.metrics = metrics
        self._subscriptions: list[object] = []

    async def start(self) -> None:
        """Start advisory subscriptions when enabled."""

        if not self.config.enabled:
            return
        subscribe = getattr(self.nats_connection, "subscribe", None)
        if not callable(subscribe):
            raise ValidationError("advisories.enabled requires a NATS connection with subscribe")
        for subject in self.config.subjects:
            subscription = subscribe(subject, cb=self._handle_message)
            if inspect.isawaitable(subscription):
                subscription = await subscription
            self._subscriptions.append(cast(object, subscription))

    async def stop(self) -> None:
        """Unsubscribe advisory subscriptions without touching sink messages."""

        for subscription in list(self._subscriptions):
            unsubscribe = getattr(subscription, "unsubscribe", None)
            if callable(unsubscribe):
                try:
                    await _maybe_await(unsubscribe())
                except Exception:
                    LOGGER.exception("failed to unsubscribe JetStream advisory monitor")
        self._subscriptions.clear()

    async def _handle_message(self, message: object) -> None:
        """NATS callback that turns an advisory message into safe metrics."""

        try:
            observe_jetstream_advisory_message(
                message,
                config=self.config,
                metrics=self.metrics,
            )
        except Exception:
            LOGGER.exception("JetStream advisory processing failed unexpectedly")
