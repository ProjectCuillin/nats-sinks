# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Documentation guardrails for the InProgress metrics runbook."""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_inprogress_runbook_is_in_public_observability_tree() -> None:
    mkdocs_config = _read(Path("mkdocs.yml"))
    observability = _read(Path("docs/observability.md"))
    metrics = _read(Path("docs/metrics.md"))
    evaluation = _read(Path("docs/in-progress-evaluation.md"))

    assert "InProgress Metrics Runbook: inprogress-metrics-runbook.md" in mkdocs_config
    assert "[InProgress Metrics Runbook](inprogress-metrics-runbook.md)" in observability
    assert "[InProgress Metrics Runbook](inprogress-metrics-runbook.md)" in metrics
    assert "[InProgress Metrics Runbook](inprogress-metrics-runbook.md)" in evaluation


def test_inprogress_runbook_documents_the_stable_metric_family() -> None:
    runbook = _read(Path("docs/inprogress-metrics-runbook.md"))

    expected_names = {
        "in_progress_attempts_total",
        "in_progress_successes_total",
        "in_progress_failures_total",
        "in_progress_max_heartbeats_reached_total",
        "current_in_progress_batches_active",
        "in_progress_heartbeat_seconds",
    }

    for name in expected_names:
        assert name in runbook
    assert "--format shell" in runbook
    assert "--format prometheus" in runbook
    assert "Not durable sink success." in runbook
    assert "does not acknowledge the original message" in runbook


def test_inprogress_runbook_examples_do_not_expose_sensitive_fields() -> None:
    runbook = _read(Path("docs/inprogress-metrics-runbook.md"))
    sensitive_examples = {
        "password=",
        "token=",
        "secret.orders",
        "oracle_secret",
        'classification="',
        'subject="',
        'payload="',
    }

    for example in sensitive_examples:
        assert example not in runbook
