# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import (
    MetricNames,
    metric_rows_from_snapshot,
    metrics_snapshot,
)
from nats_sinks.observability.otlp import build_otlp_metrics_document
from nats_sinks.observability.policy import ObservabilityPolicy
from nats_sinks.observability.prometheus import render_prometheus_textfile
from nats_sinks.observability.splunk_hec import build_splunk_hec_event
from nats_sinks.observability.statsd import render_statsd_lines
from nats_sinks.observability.subject_family import (
    aggregate_subject_family_counter,
    attach_labeled_metric_rows,
)
from nats_sinks.observability.syslog import render_syslog_messages
from nats_sinks.testing import certification_envelope


def _policy(
    *,
    subject_metrics: dict[str, object] | None = None,
    **connector_policy: object,
) -> ObservabilityPolicy:
    subject_settings = {
        "enabled": True,
        "rules": [
            {
                "subject": "orders.*",
                "label": "orders",
                "allowed_metrics": [MetricNames.MESSAGES_WRITTEN_TOTAL],
            },
            {"subject": "orders.secret", "action": "deny"},
        ],
    }
    if subject_metrics:
        subject_settings.update(subject_metrics)
    return ObservabilityPolicy(
        enabled=True,
        allowed_metrics=[MetricNames.MESSAGES_WRITTEN_TOTAL],
        subject_metrics=subject_settings,
        **connector_policy,
    )


def _snapshot_with_subject_rows(policy: ObservabilityPolicy) -> dict[str, object]:
    result = aggregate_subject_family_counter(
        (
            certification_envelope(subject="orders.created", stream_sequence=1),
            certification_envelope(subject="orders.updated", stream_sequence=2),
            certification_envelope(subject="orders.secret", stream_sequence=3),
            certification_envelope(subject="payments.created", stream_sequence=4),
        ),
        policy,
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )
    snapshot = metrics_snapshot(
        counters={MetricNames.MESSAGES_WRITTEN_TOTAL: 4},
        gauges={},
        observations={},
    )
    return attach_labeled_metric_rows(snapshot, result.rows)


def test_subject_family_counter_aggregates_only_approved_families() -> None:
    result = aggregate_subject_family_counter(
        (
            certification_envelope(subject="orders.created"),
            certification_envelope(subject="orders.updated"),
            certification_envelope(subject="orders.secret"),
            certification_envelope(subject="payments.created"),
        ),
        _policy(),
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )

    assert result.denied_messages == 2
    assert result.dropped_messages == 0
    assert result.overflowed_messages == 0
    assert len(result.rows) == 1
    assert result.rows[0].name == MetricNames.MESSAGES_WRITTEN_TOTAL
    assert result.rows[0].value == 2
    assert result.rows[0].labels == {"subject_family": "orders"}


def test_subject_family_counter_is_disabled_by_default() -> None:
    result = aggregate_subject_family_counter(
        (certification_envelope(subject="orders.created"),),
        ObservabilityPolicy(),
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )

    assert result.rows == ()
    assert result.denied_messages == 1


def test_subject_family_counter_overflow_can_aggregate_other() -> None:
    policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "max_subject_families": 1,
            "overflow_action": "aggregate_other",
            "rules": [
                {"subject": ">", "label": "all", "display_mode": "hash"},
            ],
        }
    )

    result = aggregate_subject_family_counter(
        (
            certification_envelope(subject="orders.created"),
            certification_envelope(subject="payments.created"),
        ),
        policy,
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )

    rows = {row.labels["subject_family"]: row.value for row in result.rows}
    assert len(rows) == 2
    assert "other" in rows
    assert result.overflowed_messages == 1


def test_subject_family_counter_overflow_can_drop_or_fail_closed() -> None:
    drop_policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "max_subject_families": 1,
            "overflow_action": "drop",
            "rules": [
                {"subject": ">", "label": "all", "display_mode": "hash"},
            ],
        }
    )

    drop_result = aggregate_subject_family_counter(
        (
            certification_envelope(subject="orders.created"),
            certification_envelope(subject="payments.created"),
        ),
        drop_policy,
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )
    assert len(drop_result.rows) == 1
    assert drop_result.dropped_messages == 1
    assert drop_result.overflowed_messages == 1

    fail_policy = ObservabilityPolicy(
        subject_metrics={
            "enabled": True,
            "max_subject_families": 1,
            "overflow_action": "fail_closed",
            "rules": [
                {"subject": ">", "label": "all", "display_mode": "hash"},
            ],
        }
    )
    with pytest.raises(ConfigurationError, match="cardinality"):
        aggregate_subject_family_counter(
            (
                certification_envelope(subject="orders.created"),
                certification_envelope(subject="payments.created"),
            ),
            fail_policy,
            metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
        )


def test_subject_family_snapshot_rows_remain_bounded_and_labeled() -> None:
    snapshot = _snapshot_with_subject_rows(_policy())
    rows = metric_rows_from_snapshot(snapshot)
    labeled = [row for row in rows if row.labels]

    assert len(labeled) == 1
    assert labeled[0].labels == {"subject_family": "orders"}
    rendered = json.dumps(snapshot, sort_keys=True)
    assert "orders.created" not in rendered
    assert "orders.updated" not in rendered
    assert "orders.secret" not in rendered
    assert "payments.created" not in rendered


def test_subject_family_prometheus_export_uses_approved_label_only() -> None:
    policy = _policy(prometheus={"enabled": True})
    rendered = render_prometheus_textfile(_snapshot_with_subject_rows(policy), policy)

    assert 'subject_family="orders"' in rendered
    assert "nats_sinks_messages_written_total 4" in rendered
    assert 'nats_sinks_messages_written_total{subject_family="orders"} 2' in rendered
    assert "orders.created" not in rendered
    assert "orders.secret" not in rendered


def test_subject_family_rows_are_not_exported_when_policy_is_disabled() -> None:
    enabled_policy = _policy(prometheus={"enabled": True})
    disabled_policy = ObservabilityPolicy(
        enabled=True,
        allowed_metrics=[MetricNames.MESSAGES_WRITTEN_TOTAL],
        prometheus={"enabled": True},
    )

    rendered = render_prometheus_textfile(
        _snapshot_with_subject_rows(enabled_policy),
        disabled_policy,
    )

    assert 'subject_family="orders"' not in rendered
    assert "nats_sinks_messages_written_total 4" in rendered


def test_subject_family_rows_are_available_to_observability_connectors() -> None:
    snapshot = _snapshot_with_subject_rows(_policy())

    otlp_policy = _policy(
        otlp={"enabled": True, "endpoint": "https://collector.example.test/v1/metrics"}
    )
    otlp = build_otlp_metrics_document(snapshot, otlp_policy)
    otlp_text = json.dumps(otlp, sort_keys=True)
    assert '"key": "subject_family"' in otlp_text
    assert '"stringValue": "orders"' in otlp_text

    statsd_policy = _policy(statsd={"enabled": True})
    assert "nats_sinks.messages_written_total.subject_family.orders:2|g" in render_statsd_lines(
        snapshot,
        statsd_policy,
    )

    syslog_policy = _policy(syslog={"enabled": True})
    assert 'label_subject_family="orders"' in render_syslog_messages(snapshot, syslog_policy)

    splunk_policy = _policy(
        splunk_hec={
            "enabled": True,
            "endpoint": "https://splunk-hec.example.test/services/collector/event",
            "token_env": "SPLUNK_HEC_TOKEN",
        }
    )
    splunk_event = build_splunk_hec_event(snapshot, splunk_policy)
    fields = splunk_event["fields"]
    assert isinstance(fields, dict)
    assert "metric_name:nats_sinks_messages_written_total.subject_family.orders" in fields
