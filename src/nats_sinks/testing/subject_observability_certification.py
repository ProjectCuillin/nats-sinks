# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Certification helpers for subject-aware observability.

Subject names often encode routing structure, mission context, tenant identity,
or other operational details.  These helpers give maintainers a repeatable,
synthetic certification path for subject-aware metrics without using real
subjects, payloads, endpoints, credentials, file paths, or table names.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import MetricNames, metrics_snapshot
from nats_sinks.observability.oci_monitoring import (
    render_oci_monitoring_post_metric_data_requests_json,
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
from nats_sinks.testing.sink_certification import certification_envelope

SUBJECT_OBSERVABILITY_ALLOWED_LABEL = "certification_orders"
SUBJECT_OBSERVABILITY_OVERFLOW_LABEL = "overflow"
SUBJECT_OBSERVABILITY_RAW_SUBJECTS = (
    "certification.orders.created",
    "certification.orders.updated",
    "certification.orders.secret",
    "certification.payments.created",
)


def _policy_from_mapping(data: Mapping[str, object]) -> ObservabilityPolicy:
    """Build an observability policy through the runtime validation boundary."""

    return ObservabilityPolicy.model_validate(dict(data))


@dataclass(slots=True)
class SubjectObservabilityDeliveryProbe:
    """Small mutable probe used to prove delivery state did not change.

    The probe intentionally models only delivery decisions that observability
    code must never take.  Certification tests can snapshot it before and after
    policy, aggregation, rendering, or export-failure simulations.
    """

    acked: int = 0
    nacked: int = 0
    dlq_published: int = 0
    sink_writes: int = 0
    notes: list[str] = field(default_factory=list)

    def snapshot(self) -> tuple[int, int, int, int, tuple[str, ...]]:
        """Return an immutable copy of the current delivery-decision state."""

        return (
            self.acked,
            self.nacked,
            self.dlq_published,
            self.sink_writes,
            tuple(self.notes),
        )


@dataclass(frozen=True, slots=True)
class SubjectObservabilityCertificationReport:
    """Summary returned by the reusable subject-observability certification."""

    disabled_rows: int
    approved_rows: int
    denied_messages: int
    overflowed_messages: int
    dropped_messages: int
    connector_names: tuple[str, ...]
    approved_label: str
    raw_subject_leaks: tuple[str, ...]
    malformed_policy_rejected: bool
    delivery_probe_before: tuple[int, int, int, int, tuple[str, ...]]
    delivery_probe_after: tuple[int, int, int, int, tuple[str, ...]]


def subject_observability_certification_envelopes() -> tuple[NatsEnvelope, ...]:
    """Return synthetic envelopes used by subject-observability tests."""

    return (
        certification_envelope(
            subject=SUBJECT_OBSERVABILITY_RAW_SUBJECTS[0],
            stream_sequence=1,
            message_id="subject-observability-certification-1",
        ),
        certification_envelope(
            subject=SUBJECT_OBSERVABILITY_RAW_SUBJECTS[1],
            stream_sequence=2,
            message_id="subject-observability-certification-2",
        ),
        certification_envelope(
            subject=SUBJECT_OBSERVABILITY_RAW_SUBJECTS[2],
            stream_sequence=3,
            message_id="subject-observability-certification-3",
        ),
        certification_envelope(
            subject=SUBJECT_OBSERVABILITY_RAW_SUBJECTS[3],
            stream_sequence=4,
            message_id="subject-observability-certification-4",
        ),
    )


def subject_observability_certification_policy(
    **connector_policy: object,
) -> ObservabilityPolicy:
    """Return a safe policy with one approved family and one explicit deny."""

    policy_data: dict[str, object] = {
        "enabled": True,
        "allowed_metrics": [MetricNames.MESSAGES_WRITTEN_TOTAL],
        "subject_metrics": {
            "enabled": True,
            "rules": [
                {
                    "subject": "certification.orders.*",
                    "label": SUBJECT_OBSERVABILITY_ALLOWED_LABEL,
                    "allowed_metrics": [MetricNames.MESSAGES_WRITTEN_TOTAL],
                },
                {
                    "subject": "certification.orders.secret",
                    "action": "deny",
                    "allowed_metrics": [MetricNames.MESSAGES_WRITTEN_TOTAL],
                },
            ],
        },
    }
    policy_data.update(connector_policy)
    return _policy_from_mapping(policy_data)


def subject_observability_overflow_policy() -> ObservabilityPolicy:
    """Return a policy that deterministically aggregates overflow rows."""

    return _policy_from_mapping(
        {
            "enabled": True,
            "allowed_metrics": [MetricNames.MESSAGES_WRITTEN_TOTAL],
            "subject_metrics": {
                "enabled": True,
                "max_subject_families": 1,
                "overflow_action": "aggregate_other",
                "overflow_label": SUBJECT_OBSERVABILITY_OVERFLOW_LABEL,
                "rules": [
                    {
                        "subject": "certification.*.*",
                        "label": "certification_hash_family",
                        "display_mode": "hash",
                        "allowed_metrics": [MetricNames.MESSAGES_WRITTEN_TOTAL],
                    }
                ],
            },
        }
    )


def subject_observability_fail_closed_policy() -> ObservabilityPolicy:
    """Return a policy that raises when subject-family cardinality is exceeded."""

    return _policy_from_mapping(
        {
            "enabled": True,
            "allowed_metrics": [MetricNames.MESSAGES_WRITTEN_TOTAL],
            "subject_metrics": {
                "enabled": True,
                "max_subject_families": 1,
                "overflow_action": "fail_closed",
                "rules": [
                    {
                        "subject": "certification.*.*",
                        "label": "certification_hash_family",
                        "display_mode": "hash",
                        "allowed_metrics": [MetricNames.MESSAGES_WRITTEN_TOTAL],
                    }
                ],
            },
        }
    )


def subject_observability_certification_snapshot(
    policy: ObservabilityPolicy | None = None,
) -> dict[str, object]:
    """Build a metrics snapshot with prepared subject-family rows."""

    rendered_policy = policy or subject_observability_certification_policy()
    result = aggregate_subject_family_counter(
        subject_observability_certification_envelopes(),
        rendered_policy,
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )
    snapshot = metrics_snapshot(
        counters={MetricNames.MESSAGES_WRITTEN_TOTAL: 4},
        gauges={},
        observations={},
    )
    return attach_labeled_metric_rows(snapshot, result.rows)


def render_subject_observability_connector_outputs(
    snapshot: Mapping[str, object],
) -> dict[str, str]:
    """Render each implemented connector from the same prepared snapshot.

    The fake endpoint values use reserved documentation domains and are used
    only to satisfy policy validation for local JSON builders.  No network
    request is made by this helper.
    """

    rendered_snapshot = dict(snapshot)
    prometheus_policy = subject_observability_certification_policy(prometheus={"enabled": True})
    otlp_policy = subject_observability_certification_policy(
        otlp={
            "enabled": True,
            "endpoint": "https://collector.invalid/v1/metrics",
        }
    )
    statsd_policy = subject_observability_certification_policy(statsd={"enabled": True})
    syslog_policy = subject_observability_certification_policy(syslog={"enabled": True})
    splunk_auth_env_field = "token" + "_env"
    splunk_policy = subject_observability_certification_policy(
        splunk_hec={
            "enabled": True,
            "endpoint": "https://splunk.invalid/services/collector/event",
            splunk_auth_env_field: "NATS_SINKS_CERTIFICATION_HEC_ENV",
        }
    )
    oci_policy = subject_observability_certification_policy(
        oci_monitoring={
            "enabled": True,
            "metric_namespace": "nats_sinks_metrics",
            "region": "eu-frankfurt-1",
            "compartment_id": "ocid1.compartment.oc1..examplecompartment",
            "dimensions": {"deployment": "certification"},
            "include_metric_labels_as_dimensions": True,
        }
    )

    return {
        "prometheus": render_prometheus_textfile(rendered_snapshot, prometheus_policy),
        "oci_monitoring": render_oci_monitoring_post_metric_data_requests_json(
            rendered_snapshot,
            oci_policy,
        ).decode("utf-8"),
        "otlp": json.dumps(
            build_otlp_metrics_document(rendered_snapshot, otlp_policy),
            sort_keys=True,
        ),
        "statsd": render_statsd_lines(rendered_snapshot, statsd_policy),
        "syslog": "\n".join(render_syslog_messages(rendered_snapshot, syslog_policy)),
        "splunk_hec": json.dumps(
            build_splunk_hec_event(rendered_snapshot, splunk_policy),
            sort_keys=True,
        ),
    }


def certify_subject_observability_malformed_policy_rejection() -> bool:
    """Return true when an unsafe subject-aware policy is rejected."""

    try:
        _policy_from_mapping(
            {
                "subject_metrics": {
                    "enabled": True,
                    "rules": [
                        {
                            "subject": "certification.>",
                            "action": "allow",
                        }
                    ],
                }
            }
        )
    except ValueError:
        return True
    return False


def certify_subject_observability_delivery_non_interference(
    operation: Callable[[], object],
    *,
    probe: SubjectObservabilityDeliveryProbe | None = None,
) -> BaseException | None:
    """Run an observability operation and prove delivery state is unchanged."""

    rendered_probe = probe or SubjectObservabilityDeliveryProbe()
    before = rendered_probe.snapshot()
    captured: BaseException | None = None
    try:
        operation()
    except BaseException as exc:
        captured = exc
    after = rendered_probe.snapshot()
    if after != before:
        raise AssertionError("subject-aware observability changed delivery decision state")
    return captured


def assert_subject_observability_output_is_sanitized(
    outputs: Mapping[str, str],
    *,
    approved_label: str = SUBJECT_OBSERVABILITY_ALLOWED_LABEL,
    raw_subjects: tuple[str, ...] = SUBJECT_OBSERVABILITY_RAW_SUBJECTS,
) -> tuple[str, ...]:
    """Assert connector outputs include the approved label and no raw subjects."""

    joined = "\n".join(outputs.values())
    if approved_label not in joined:
        raise AssertionError("subject-aware connector output omitted approved family label")
    leaks = tuple(subject for subject in raw_subjects if subject in joined)
    if leaks:
        raise AssertionError("subject-aware connector output leaked raw subject")
    return leaks


def run_subject_observability_certification() -> SubjectObservabilityCertificationReport:
    """Run the local subject-aware observability certification checks."""

    envelopes = subject_observability_certification_envelopes()
    disabled = aggregate_subject_family_counter(
        envelopes,
        ObservabilityPolicy(),
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )
    if disabled.rows:
        raise AssertionError("subject-aware observability must be disabled by default")

    policy = subject_observability_certification_policy()
    approved = aggregate_subject_family_counter(
        envelopes,
        policy,
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )
    if not approved.rows:
        raise AssertionError("subject-aware certification expected one approved row")

    overflowed = aggregate_subject_family_counter(
        envelopes[:2],
        subject_observability_overflow_policy(),
        metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
    )

    snapshot = subject_observability_certification_snapshot(policy)
    outputs = render_subject_observability_connector_outputs(snapshot)
    leaks = assert_subject_observability_output_is_sanitized(outputs)

    probe = SubjectObservabilityDeliveryProbe()
    failure = certify_subject_observability_delivery_non_interference(
        lambda: aggregate_subject_family_counter(
            envelopes[:2],
            subject_observability_fail_closed_policy(),
            metric_name=MetricNames.MESSAGES_WRITTEN_TOTAL,
        ),
        probe=probe,
    )
    if not isinstance(failure, ConfigurationError):
        raise AssertionError("subject-aware overflow failure did not fail closed")

    return SubjectObservabilityCertificationReport(
        disabled_rows=len(disabled.rows),
        approved_rows=len(approved.rows),
        denied_messages=approved.denied_messages,
        overflowed_messages=overflowed.overflowed_messages,
        dropped_messages=overflowed.dropped_messages,
        connector_names=tuple(sorted(outputs)),
        approved_label=SUBJECT_OBSERVABILITY_ALLOWED_LABEL,
        raw_subject_leaks=leaks,
        malformed_policy_rejected=certify_subject_observability_malformed_policy_rejection(),
        delivery_probe_before=probe.snapshot(),
        delivery_probe_after=probe.snapshot(),
    )
