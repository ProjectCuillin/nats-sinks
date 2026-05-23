# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Event freshness observations for JetStream messages.

Freshness metrics answer a narrow operational question: how old was an event
when `nats-sinks` received it, and how old was it after the destination sink
reported durable success?  The values are intentionally aggregate-only.  They do
not add subject, source, system, table, sensor, mission, tenant, or endpoint
labels because those dimensions can reveal sensitive operational patterns when
exported to Prometheus, OpenTelemetry, shell scripts, or log processors.

These metrics are observational.  They never decide ACK, NAK, DLQ, routing, or
policy behavior.  Future releases may add explicit policy gates for stale event
rejection, but this module must remain safe to call in the current commit-then-
acknowledge path: if freshness observation fails, delivery semantics continue.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from nats_sinks.core.metadata import datetime_to_epoch_ns, resolve_message_created_timestamp
from nats_sinks.core.metrics import MetricNames, MetricsRecorder, increment_metric, observe_metric

if TYPE_CHECKING:
    from collections.abc import Sequence

    from nats_sinks.core.envelope import NatsEnvelope

LOGGER = logging.getLogger(__name__)


def _non_negative_age_seconds(later_epoch_ns: int, earlier_epoch_ns: int) -> float:
    """Return age seconds while protecting metric histograms from negative ages."""

    return max(0.0, (later_epoch_ns - earlier_epoch_ns) / 1_000_000_000)


def _future_skew_seconds(created_epoch_ns: int, observed_epoch_ns: int) -> float:
    """Return positive source clock skew in seconds for future-dated events."""

    return max(0.0, (created_epoch_ns - observed_epoch_ns) / 1_000_000_000)


def record_event_freshness_metrics(
    metrics: MetricsRecorder,
    envelopes: Sequence[NatsEnvelope],
    *,
    stored_at: datetime,
    enabled: bool,
    stale_after_seconds: float | None,
    future_skew_tolerance_seconds: float,
) -> None:
    """Record aggregate freshness metrics for a successfully committed batch.

    The runner calls this after `sink.write_batch` returns success and before it
    ACKs the source messages.  Metric observation must never become a delivery
    decision, so this function catches and logs unexpected metric failures
    without exposing payloads, subjects, headers, or downstream identifiers.
    """

    if not enabled:
        return

    try:
        stored_at_epoch_ns = datetime_to_epoch_ns(stored_at)
        if stored_at_epoch_ns is None:
            return

        for envelope in envelopes:
            created = resolve_message_created_timestamp(envelope)
            received_at_epoch_ns = datetime_to_epoch_ns(envelope.received_at)

            if created.malformed_header:
                increment_metric(metrics, MetricNames.EVENT_CREATION_TIMESTAMP_MALFORMED_TOTAL)

            if created.epoch_ns is None or received_at_epoch_ns is None:
                increment_metric(metrics, MetricNames.EVENT_CREATION_TIMESTAMP_MISSING_TOTAL)
                continue

            receive_skew_seconds = _future_skew_seconds(
                created.epoch_ns,
                received_at_epoch_ns,
            )
            if receive_skew_seconds > 0:
                observe_metric(
                    metrics, MetricNames.EVENT_SOURCE_CLOCK_SKEW_SECONDS, receive_skew_seconds
                )
                if receive_skew_seconds > future_skew_tolerance_seconds:
                    increment_metric(metrics, MetricNames.EVENT_CREATION_TIMESTAMP_FUTURE_TOTAL)

            receive_age_seconds = _non_negative_age_seconds(
                received_at_epoch_ns,
                created.epoch_ns,
            )
            store_age_seconds = _non_negative_age_seconds(
                stored_at_epoch_ns,
                created.epoch_ns,
            )
            observe_metric(metrics, MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS, receive_age_seconds)
            observe_metric(metrics, MetricNames.EVENT_AGE_AT_STORE_SECONDS, store_age_seconds)

            if stale_after_seconds is not None:
                if receive_age_seconds >= stale_after_seconds:
                    increment_metric(metrics, MetricNames.EVENTS_STALE_AT_RECEIVE_TOTAL)
                if store_age_seconds >= stale_after_seconds:
                    increment_metric(metrics, MetricNames.EVENTS_STALE_AT_STORE_TOTAL)
    except Exception:
        LOGGER.warning("event freshness metric observation failed", exc_info=True)
