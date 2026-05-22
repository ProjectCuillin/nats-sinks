# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for issue #64 Oracle benchmark phase-rate reporting.

Oracle benchmark reports are public-safe operator evidence.  Message-processing
phases can expose messages-per-second because they represent work over the
configured benchmark message count.  Retry-delay and shutdown phases are
lifecycle timing observations, so they should not claim message throughput
unless a future benchmark records explicit phase-specific work counts.
"""

from __future__ import annotations

from nats_sinks.core.metrics import InMemoryMetrics, MetricNames, observe_metric
from nats_sinks.testing import OracleBenchmarkOptions, build_oracle_benchmark_report


def test_bug_64_retry_and_shutdown_do_not_report_message_rates() -> None:
    """Retry-delay and shutdown phases should render timing only."""

    metrics = InMemoryMetrics()
    observe_metric(metrics, MetricNames.NATS_FETCH_SECONDS, 0.25)
    observe_metric(metrics, MetricNames.ORACLE_EXECUTE_SECONDS, 0.50)
    observe_metric(metrics, MetricNames.RETRY_BACKOFF_DELAY_SECONDS, 1.25)
    report = build_oracle_benchmark_report(
        options=OracleBenchmarkOptions(message_count=100, batch_size=25),
        metrics=metrics,
        explicit_phase_seconds={"publish": [0.40], "shutdown": [0.02]},
    )
    phases = {phase["phase"]: phase for phase in report.to_dict()["phases"]}

    assert phases["publish"]["messages_per_second"] == 250.0
    assert phases["fetch"]["messages_per_second"] == 400.0
    assert phases["write"]["messages_per_second"] == 200.0
    assert "messages_per_second" not in phases["retry"]
    assert "messages_per_second" not in phases["shutdown"]
