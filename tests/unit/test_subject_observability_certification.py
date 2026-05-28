# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nats_sinks.cli.metrics import app as metrics_app
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricNames, write_metrics_snapshot
from nats_sinks.observability.subject_family import aggregate_subject_family_counter
from nats_sinks.testing import (
    SUBJECT_OBSERVABILITY_ALLOWED_LABEL,
    SUBJECT_OBSERVABILITY_RAW_SUBJECTS,
    SubjectObservabilityDeliveryProbe,
    assert_subject_observability_output_is_sanitized,
    certify_subject_observability_delivery_non_interference,
    certify_subject_observability_malformed_policy_rejection,
    render_subject_observability_connector_outputs,
    run_subject_observability_certification,
    subject_observability_certification_envelopes,
    subject_observability_certification_policy,
    subject_observability_certification_snapshot,
    subject_observability_fail_closed_policy,
    subject_observability_overflow_policy,
)

runner = CliRunner()


def test_subject_observability_certification_report_covers_release_gate() -> None:
    report = run_subject_observability_certification()

    assert report.disabled_rows == 0
    assert report.approved_rows == 1
    assert report.denied_messages == 2
    assert report.overflowed_messages == 1
    assert report.dropped_messages == 0
    assert report.raw_subject_leaks == ()
    assert report.malformed_policy_rejected is True
    assert report.delivery_probe_before == report.delivery_probe_after
    assert set(report.connector_names) == {
        "oci_monitoring",
        "otlp",
        "prometheus",
        "splunk_hec",
        "statsd",
        "syslog",
    }


def test_subject_observability_disabled_by_default() -> None:
    report = run_subject_observability_certification()

    assert report.disabled_rows == 0


def test_subject_observability_allows_denies_and_caps_subject_families() -> None:
    envelopes = subject_observability_certification_envelopes()
    policy = subject_observability_certification_policy()

    result = aggregate_subject_family_counter(
        envelopes,
        policy,
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )

    assert len(result.rows) == 1
    assert result.rows[0].labels == {"subject_family": SUBJECT_OBSERVABILITY_ALLOWED_LABEL}
    assert result.rows[0].value == 2
    assert result.denied_messages == 2

    overflow = aggregate_subject_family_counter(
        envelopes[:2],
        subject_observability_overflow_policy(),
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )
    labels = {row.labels["subject_family"] for row in overflow.rows}
    assert "overflow" in labels
    assert overflow.overflowed_messages == 1


def test_subject_observability_rejects_malformed_policy() -> None:
    assert certify_subject_observability_malformed_policy_rejection() is True


def test_subject_observability_outputs_are_sanitized_across_connectors() -> None:
    outputs = render_subject_observability_connector_outputs(
        subject_observability_certification_snapshot()
    )

    assert_subject_observability_output_is_sanitized(outputs)
    joined = "\n".join(outputs.values())
    assert SUBJECT_OBSERVABILITY_ALLOWED_LABEL in joined
    for raw_subject in SUBJECT_OBSERVABILITY_RAW_SUBJECTS:
        assert raw_subject not in joined


def test_subject_observability_cli_reads_prepared_rows_without_raw_subjects(
    tmp_path: Path,
) -> None:
    snapshot_path = tmp_path / "subject-observability-metrics.json"
    write_metrics_snapshot(subject_observability_certification_snapshot(), snapshot_path)

    table = runner.invoke(metrics_app, ["show", str(snapshot_path), "--metric", "messages_*"])
    assert table.exit_code == 0
    assert f"subject_family={SUBJECT_OBSERVABILITY_ALLOWED_LABEL}" in table.stdout

    shell = runner.invoke(
        metrics_app,
        ["show", str(snapshot_path), "--format", "shell", "--metric", "messages_*"],
    )
    assert shell.exit_code == 0
    assert "MESSAGES_WRITTEN_TOTAL_SUBJECT_FAMILY_CERTIFICATION_ORDERS=2" in shell.stdout

    prometheus = runner.invoke(
        metrics_app,
        ["show", str(snapshot_path), "--format", "prometheus", "--metric", "messages_*"],
    )
    assert prometheus.exit_code == 0
    assert (
        'nats_sinks_messages_written_total{subject_family="certification_orders"} 2'
        in prometheus.stdout
    )

    data = runner.invoke(
        metrics_app,
        ["show", str(snapshot_path), "--format", "json", "--metric", "messages_*"],
    )
    assert data.exit_code == 0
    payload = json.loads(data.stdout)
    assert any(
        metric["labels"] == {"subject_family": SUBJECT_OBSERVABILITY_ALLOWED_LABEL}
        for metric in payload["metrics"]
    )

    combined_output = "\n".join((table.stdout, shell.stdout, prometheus.stdout, data.stdout))
    for raw_subject in SUBJECT_OBSERVABILITY_RAW_SUBJECTS:
        assert raw_subject not in combined_output


def test_subject_observability_failures_do_not_change_delivery_decisions() -> None:
    probe = SubjectObservabilityDeliveryProbe()
    before = probe.snapshot()

    failure = certify_subject_observability_delivery_non_interference(
        lambda: aggregate_subject_family_counter(
            subject_observability_certification_envelopes()[:2],
            subject_observability_fail_closed_policy(),
            metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
        ),
        probe=probe,
    )

    assert isinstance(failure, ConfigurationError)
    assert probe.snapshot() == before


def test_subject_observability_delivery_probe_detects_contract_breaks() -> None:
    probe = SubjectObservabilityDeliveryProbe()

    def unsafe_operation() -> None:
        probe.acked += 1

    with pytest.raises(AssertionError, match="delivery decision"):
        certify_subject_observability_delivery_non_interference(unsafe_operation, probe=probe)
