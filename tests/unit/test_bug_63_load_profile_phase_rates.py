# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for issue #63 load-profile phase-rate accounting.

The load-profile report is used as public release evidence, so each phase must
calculate throughput from the amount of work that phase actually handled.  A
shutdown profile may intentionally leave some generated messages unfetched, and
a DLQ profile may intentionally route malformed messages away from the backend
write path.  In both cases, using the total generated message count inflates
phase rates and gives maintainers misleading performance evidence.
"""

from __future__ import annotations

import pytest

from nats_sinks.testing import LoadProfileOptions, LoadProfileReport, run_load_profile


def _phase(report: LoadProfileReport, name: str):
    """Return one phase timing from a report by its stable phase name."""

    return {phase.phase: phase for phase in report.phases}[name]


def test_bug_63_shutdown_fetch_rate_uses_fetched_messages() -> None:
    """Shutdown fetch throughput must not count messages that were never fetched."""

    report = run_load_profile(
        LoadProfileOptions(profile="shutdown", message_count=10, batch_size=4)
    )
    fetch = _phase(report, "fetch")

    assert report.counters["messages_generated"] == 10
    assert report.counters["messages_fetched"] == 8
    assert report.counters["shutdown_unfetched_messages"] == 2
    assert fetch.total_seconds > 0
    assert fetch.messages_per_second == pytest.approx(
        report.counters["messages_fetched"] / fetch.total_seconds
    )


def test_bug_63_dlq_and_backend_rates_use_phase_specific_counts() -> None:
    """DLQ and backend-write throughput should use their own completed counts."""

    report = run_load_profile(LoadProfileOptions(profile="dlq", message_count=18, batch_size=6))
    dlq = _phase(report, "dlq")
    backend_write = _phase(report, "backend_write")

    assert report.counters["messages_generated"] == 18
    assert 0 < report.counters["messages_dlq"] < report.counters["messages_generated"]
    assert report.counters["messages_written"] < report.counters["messages_generated"]
    assert dlq.total_seconds > 0
    assert backend_write.total_seconds > 0
    assert dlq.messages_per_second == pytest.approx(
        report.counters["messages_dlq"] / dlq.total_seconds
    )
    assert backend_write.messages_per_second == pytest.approx(
        report.counters["messages_written"] / backend_write.total_seconds
    )
