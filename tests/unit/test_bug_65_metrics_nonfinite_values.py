# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for issue #65 metrics snapshot JSON correctness."""

from __future__ import annotations

import math

import pytest

from nats_sinks.core.metrics import (
    METRICS_SNAPSHOT_SCHEMA,
    InMemoryMetrics,
    MetricNames,
    load_metrics_snapshot,
    observe_metric,
    set_metric_value,
    write_metrics_snapshot,
)


def test_bug_65_metrics_snapshot_rejects_nonfinite_gauge() -> None:
    """Metric snapshots should never serialize NaN gauges as JSON."""

    metrics = InMemoryMetrics()
    set_metric_value(metrics, MetricNames.CURRENT_BATCH_MESSAGES, math.nan)

    with pytest.raises(ValueError, match="finite"):
        metrics.snapshot()


def test_bug_65_metrics_snapshot_rejects_nonfinite_observation() -> None:
    """Metric histogram observations must be finite before snapshot output."""

    metrics = InMemoryMetrics()
    observe_metric(metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, math.inf)

    with pytest.raises(ValueError, match="finite"):
        metrics.snapshot()


def test_bug_65_load_metrics_snapshot_rejects_nonstandard_constants(tmp_path) -> None:
    """Snapshot loading should reject Python JSON extensions such as NaN."""

    snapshot = tmp_path / "metrics.json"
    snapshot.write_text(
        (
            f'{{"schema":"{METRICS_SNAPSHOT_SCHEMA}",'
            '"namespace":"nats_sinks","generated_at_epoch_seconds":NaN,'
            '"counters":{},"gauges":{},"observations":{}}\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not valid JSON"):
        load_metrics_snapshot(snapshot)


def test_bug_65_write_metrics_snapshot_rejects_nonfinite_values(tmp_path) -> None:
    """Snapshot writing should use standards-compliant JSON output."""

    with pytest.raises(ValueError):
        write_metrics_snapshot(
            {
                "schema": METRICS_SNAPSHOT_SCHEMA,
                "namespace": "nats_sinks",
                "generated_at_epoch_seconds": math.nan,
                "counters": {},
                "gauges": {},
                "observations": {},
            },
            tmp_path / "metrics.json",
        )
