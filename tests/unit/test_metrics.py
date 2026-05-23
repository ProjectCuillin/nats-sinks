# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Metrics contract tests.

Metrics are operational signals, not delivery decisions.  These tests keep the
names stable and make sure the helper functions remain deterministic for
exporters, embedded applications, and the runner tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nats_sinks.core.config import MetricsConfig
from nats_sinks.core.metrics import (
    DEFAULT_METRIC_NAMESPACE,
    LEGACY_METRIC_ALIASES,
    METRIC_SPECS,
    InMemoryMetrics,
    JsonFileMetrics,
    MetricNames,
    increment_metric,
    load_metrics_snapshot,
    metric_rows_from_snapshot,
    observe_metric,
    qualified_metric_name,
    set_metric_value,
    validate_metric_namespace,
)


def test_metric_specs_have_unique_names_and_kinds() -> None:
    names = [spec.name for spec in METRIC_SPECS]

    assert len(names) == len(set(names))
    assert MetricNames.MESSAGES_FETCHED_TOTAL in names
    assert MetricNames.SINK_BATCH_WRITE_SECONDS in names
    assert MetricNames.MESSAGES_TERMINATED_TOTAL in names
    assert MetricNames.TERM_ERRORS_TOTAL in names
    assert MetricNames.PRIORITY_LANE_MESSAGES_TOTAL in names
    assert MetricNames.CURRENT_PRIORITY_LANES_ACTIVE in names
    assert MetricNames.POLICY_MESSAGES_PASSED_TOTAL in names
    assert MetricNames.POLICY_MESSAGES_REJECTED_TOTAL in names
    assert MetricNames.POLICY_EVALUATION_ERRORS_TOTAL in names
    assert MetricNames.SIZE_POLICY_MESSAGES_PASSED_TOTAL in names
    assert MetricNames.SIZE_POLICY_MESSAGES_REJECTED_TOTAL in names
    assert MetricNames.SIZE_POLICY_EVALUATION_ERRORS_TOTAL in names
    assert MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS in names
    assert MetricNames.EVENT_AGE_AT_STORE_SECONDS in names
    assert MetricNames.EVENT_CREATION_TIMESTAMP_MISSING_TOTAL in names
    assert MetricNames.EVENT_SOURCE_CLOCK_SKEW_SECONDS in names
    assert MetricNames.ORACLE_DUPLICATES_TOTAL in names
    assert MetricNames.ORACLE_DUPLICATE_NOOP_TOTAL in names
    assert MetricNames.ORACLE_MERGE_ROWS_TOTAL in names
    assert MetricNames.ORACLE_MERGE_OUTCOME_UNKNOWN_TOTAL in names
    assert MetricNames.JETSTREAM_ADVISORIES_RECEIVED_TOTAL in names
    assert MetricNames.JETSTREAM_ADVISORY_MAX_DELIVER_TOTAL in names
    assert {spec.kind for spec in METRIC_SPECS} == {"counter", "histogram", "gauge"}


def test_qualified_metric_names_use_configured_namespace() -> None:
    assert (
        qualified_metric_name(MetricNames.MESSAGES_FETCHED_TOTAL)
        == "nats_sinks_messages_fetched_total"
    )
    assert (
        qualified_metric_name(
            MetricNames.SINK_BATCH_WRITE_SECONDS,
            namespace="mission_ops",
        )
        == "mission_ops_sink_batch_write_seconds"
    )


@pytest.mark.parametrize(
    ("namespace", "expected"),
    [
        ("nats_sinks", "nats_sinks"),
        ("mission_ops", "mission_ops"),
        ("ops:telemetry", "ops:telemetry"),
        ("  nats_sinks  ", DEFAULT_METRIC_NAMESPACE),
    ],
)
def test_metric_namespace_validation_accepts_exporter_safe_names(
    namespace: str,
    expected: str,
) -> None:
    assert validate_metric_namespace(namespace) == expected


@pytest.mark.parametrize("namespace", ["", " ", "1starts_with_digit", "has-dash"])
def test_metric_namespace_validation_rejects_unsafe_names(namespace: str) -> None:
    with pytest.raises(ValueError, match="metrics namespace"):
        validate_metric_namespace(namespace)


def test_metrics_config_validates_namespace() -> None:
    assert MetricsConfig(namespace="mission_ops").namespace == "mission_ops"

    with pytest.raises(ValueError, match="metrics namespace"):
        MetricsConfig(namespace="mission-ops")


def test_metrics_config_validates_snapshot_file() -> None:
    assert MetricsConfig(snapshot_file="  .local/metrics.json  ").snapshot_file == (
        ".local/metrics.json"
    )

    with pytest.raises(ValueError, match="snapshot_file"):
        MetricsConfig(snapshot_file="\n")


def test_metrics_config_validates_freshness_thresholds() -> None:
    config = MetricsConfig(
        event_freshness_enabled=True,
        event_stale_after_seconds=120.0,
        event_future_skew_tolerance_seconds=10.0,
    )

    assert config.event_stale_after_seconds == 120.0
    assert config.event_future_skew_tolerance_seconds == 10.0

    with pytest.raises(ValueError, match="event_stale_after_seconds"):
        MetricsConfig(event_stale_after_seconds=-1)

    with pytest.raises(ValueError, match="event_future_skew_tolerance_seconds"):
        MetricsConfig(event_future_skew_tolerance_seconds=-1)


def test_in_memory_metrics_records_canonical_names_and_legacy_aliases() -> None:
    metrics = InMemoryMetrics()

    increment_metric(metrics, MetricNames.MESSAGES_PREPARED_TOTAL, 2)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.125)
    set_metric_value(metrics, MetricNames.CURRENT_BATCH_MESSAGES, 2.0)
    metrics.mark_success()

    assert metrics.counters[MetricNames.MESSAGES_PREPARED_TOTAL] == 2
    assert metrics.counters[MetricNames.LEGACY_MESSAGES_RECEIVED_TOTAL] == 2
    assert metrics.observations[MetricNames.SINK_BATCH_WRITE_SECONDS] == [0.125]
    assert metrics.observations[MetricNames.LEGACY_BATCH_WRITE_SECONDS] == [0.125]
    assert metrics.gauges[MetricNames.CURRENT_BATCH_MESSAGES] == 2.0
    assert metrics.gauges[MetricNames.LEGACY_CURRENT_BATCH_SIZE] == 2.0
    assert MetricNames.LEGACY_LAST_SUCCESS_TIMESTAMP in metrics.gauges
    assert MetricNames.LAST_SINK_SUCCESS_EPOCH_SECONDS in metrics.gauges


def test_json_file_metrics_writes_sanitized_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    metrics = JsonFileMetrics(path, namespace="mission_ops")

    increment_metric(metrics, MetricNames.MESSAGES_PREPARED_TOTAL, 3)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATES_TOTAL, 1)
    increment_metric(metrics, MetricNames.ORACLE_DUPLICATE_NOOP_TOTAL, 1)
    increment_metric(metrics, MetricNames.ORACLE_MERGE_ROWS_TOTAL, 3)
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, 0.5)
    set_metric_value(metrics, MetricNames.CURRENT_BATCH_MESSAGES, 3.0)

    snapshot = load_metrics_snapshot(path)
    rows = metric_rows_from_snapshot(snapshot)
    row_by_name = {row.name: row for row in rows}

    assert snapshot["namespace"] == "mission_ops"
    assert row_by_name[MetricNames.MESSAGES_PREPARED_TOTAL].value == 3
    assert row_by_name[MetricNames.ORACLE_DUPLICATES_TOTAL].value == 1
    assert row_by_name[MetricNames.ORACLE_DUPLICATE_NOOP_TOTAL].value == 1
    assert row_by_name[MetricNames.ORACLE_MERGE_ROWS_TOTAL].value == 3
    assert MetricNames.LEGACY_MESSAGES_RECEIVED_TOTAL not in row_by_name
    assert row_by_name[f"{MetricNames.SINK_BATCH_WRITE_SECONDS}.count"].value == 1
    assert row_by_name[MetricNames.CURRENT_BATCH_MESSAGES].value == 3.0


def test_load_metrics_snapshot_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    path.write_text(
        (
            '{"schema":"nats_sinks.metrics.snapshot.v1",'
            '"schema":"nats_sinks.metrics.snapshot.v1",'
            '"namespace":"nats_sinks","generated_at_epoch_seconds":1,'
            '"counters":{},"gauges":{},"observations":{}}\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key"):
        load_metrics_snapshot(path)


def test_legacy_aliases_point_to_existing_canonical_metrics() -> None:
    canonical_names = {spec.name for spec in METRIC_SPECS}

    assert LEGACY_METRIC_ALIASES
    assert set(LEGACY_METRIC_ALIASES).issubset(canonical_names)


def test_unknown_metric_name_is_rejected_for_qualified_output() -> None:
    with pytest.raises(ValueError, match="unknown nats-sinks metric name"):
        qualified_metric_name("not_a_project_metric")
