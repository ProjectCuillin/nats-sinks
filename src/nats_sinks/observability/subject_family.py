# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Bounded subject-family metric aggregation.

Subject names can reveal routing structure and can create high-cardinality
metric sets.  This module never derives labels directly in exporters.  Instead,
it prepares a small, reviewed `subject_family` series from normalized
``NatsEnvelope`` objects and the disabled-by-default `subject_metrics` policy.
Future and existing observability connectors can then consume the prepared
series without seeing raw message subjects by default.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import METRIC_SPEC_BY_NAME, MetricRow
from nats_sinks.observability.policy import (
    ObservabilityPolicy,
    SubjectAwareObservabilityPolicy,
    evaluate_subject_observability_policy,
)

SUBJECT_FAMILY_LABEL_NAME = "subject_family"
LABELED_METRICS_SNAPSHOT_KEY = "labeled_metrics"


@dataclass(frozen=True, slots=True)
class SubjectFamilyAggregationResult:
    """Safe summary of one subject-family aggregation pass."""

    rows: tuple[MetricRow, ...]
    denied_messages: int = 0
    dropped_messages: int = 0
    overflowed_messages: int = 0


def aggregate_subject_family_counter(
    envelopes: Iterable[NatsEnvelope],
    policy: ObservabilityPolicy | SubjectAwareObservabilityPolicy,
    *,
    metric_name: str,
) -> SubjectFamilyAggregationResult:
    """Aggregate a counter metric into approved subject-family rows.

    The function is observational only.  It does not ACK, NAK, retry, publish
    DLQ records, mutate envelopes, or write to any sink.  Disabled or denied
    policies produce no rows.  Overflow is handled according to the reviewed
    `subject_metrics.overflow_action` value.
    """

    if metric_name not in METRIC_SPEC_BY_NAME:
        raise ValueError(f"unknown nats-sinks metric name: {metric_name}")
    spec = METRIC_SPEC_BY_NAME[metric_name]
    if spec.kind != "counter":
        raise ValueError("subject-family aggregation currently supports counter metrics")

    subject_policy = policy.subject_metrics if isinstance(policy, ObservabilityPolicy) else policy
    counts: Counter[str] = Counter()
    denied_messages = 0
    dropped_messages = 0
    overflowed_messages = 0

    for envelope in envelopes:
        decision = evaluate_subject_observability_policy(
            subject_policy,
            subject=envelope.subject,
            metric_name=metric_name,
        )
        if not decision.allowed or decision.label is None:
            denied_messages += 1
            continue

        label = decision.label
        if label in counts or len(counts) < subject_policy.max_subject_families:
            counts[label] += 1
            continue

        overflowed_messages += 1
        if subject_policy.overflow_action == "aggregate_other":
            counts[subject_policy.overflow_label] += 1
        elif subject_policy.overflow_action == "drop":
            dropped_messages += 1
        else:
            raise ConfigurationError(
                "subject-family metric cardinality exceeded max_subject_families"
            )

    rows = tuple(
        MetricRow(
            kind="counter",
            name=metric_name,
            value=float(count),
            description=spec.description,
            labels={SUBJECT_FAMILY_LABEL_NAME: label},
        )
        for label, count in sorted(counts.items())
    )
    return SubjectFamilyAggregationResult(
        rows=rows,
        denied_messages=denied_messages,
        dropped_messages=dropped_messages,
        overflowed_messages=overflowed_messages,
    )


def attach_labeled_metric_rows(
    snapshot: dict[str, object],
    rows: Iterable[MetricRow],
) -> dict[str, object]:
    """Return a copy of a metrics snapshot with prepared labeled rows attached."""

    rendered_rows = [_serialize_labeled_metric_row(row) for row in rows]
    updated = dict(snapshot)
    existing = updated.get(LABELED_METRICS_SNAPSHOT_KEY, [])
    if existing:
        if not isinstance(existing, list):
            raise ValueError("existing labeled_metrics snapshot section must be a list")
        rendered_rows = [*existing, *rendered_rows]
    updated[LABELED_METRICS_SNAPSHOT_KEY] = rendered_rows
    return updated


def _serialize_labeled_metric_row(row: MetricRow) -> dict[str, Any]:
    """Serialize one prepared labeled row into the snapshot extension shape."""

    if not row.labels:
        raise ValueError("labeled metric rows must include labels")
    if row.kind not in {"counter", "gauge"}:
        raise ValueError("labeled metric rows may only be counters or gauges")
    return {
        "kind": row.kind,
        "name": row.name,
        "value": row.value,
        "labels": dict(sorted(row.labels.items())),
    }
