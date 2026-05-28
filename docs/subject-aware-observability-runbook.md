# Subject-Aware Observability Runbook

Subject-aware observability is a controlled way to expose approved
subject-family metrics without exporting raw NATS subjects. It is useful when
operators need a higher-level operational view, for example separating
synthetic `certification.orders.*` traffic from other test flows, while still
keeping payloads, message IDs, concrete subjects, credentials, file paths, and
destination details out of monitoring systems.

This runbook is deliberately conservative. Subject-aware export is disabled by
default, must use reviewed low-cardinality family labels, and must never affect
message delivery, ACK behavior, retries, DLQ publication, or sink writes.

## When Not To Enable It

Do not enable subject-aware export when:

- NATS subjects contain sensitive platform names, customer names, mission
  identifiers, tenant IDs, operational locations, or other context that has not
  been approved for monitoring.
- The proposed label would still reveal sensitive meaning after redaction.
- The subject family count is not naturally small and stable.
- Operators cannot define a deterministic overflow policy.
- The deployment does not have a clear owner for reviewing observability
  policy changes.
- The monitoring platform is shared with teams that should not see traffic
  family names.
- Subject-family metrics would be used as delivery evidence. They are
  observability signals only.

If in doubt, keep the default aggregate-only metrics. Aggregate counters still
show throughput, failures, ACK behavior, DLQ activity, and backend progress
without adding subject-family labels.

## Certification Boundary

The certification helpers live in `nats_sinks.testing` and use synthetic data
only. They prove the safety properties that maintainers and connector authors
must preserve:

- subject-aware export is disabled by default;
- allow and deny rules behave predictably;
- malformed policies are rejected;
- cardinality caps and overflow behavior are deterministic;
- connector output contains approved family labels but not raw subjects;
- observability failures do not change delivery decisions;
- Prometheus, OTLP, OCI Monitoring, StatsD, syslog, Splunk HEC, and
- Prometheus, OTLP, StatsD, Amazon CloudWatch, syslog, Splunk HEC, and
  `nats-sink-metrics` render the same prepared low-cardinality rows.

Run the focused certification suite with:

```bash
python -m pytest tests/unit/test_subject_observability_certification.py -q
```

Run the normal release validation with:

```bash
scripts/check.sh
```

## Safe Policy Shape

Start with subject-aware export disabled:

```json
{
  "subject_metrics": {
    "enabled": false,
    "default_action": "deny",
    "max_subject_families": 20,
    "overflow_action": "drop",
    "overflow_label": "other",
    "allow_raw_subjects": false,
    "rules": []
  }
}
```

Enable subject-family metrics only after review. This example uses synthetic
subjects and a stable family label:

```json
{
  "enabled": true,
  "allowed_metrics": [
    "messages_written_total"
  ],
  "subject_metrics": {
    "enabled": true,
    "default_action": "deny",
    "max_subject_families": 20,
    "overflow_action": "aggregate_other",
    "overflow_label": "other",
    "allow_raw_subjects": false,
    "rules": [
      {
        "subject": "certification.orders.*",
        "action": "allow",
        "label": "certification_orders",
        "display_mode": "label",
        "allowed_metrics": [
          "messages_written_total"
        ]
      },
      {
        "subject": "certification.orders.secret",
        "action": "deny"
      }
    ]
  }
}
```

The allow rule uses `certification_orders`, not the raw subject. The deny rule
is explicit and takes precedence even if the broader allow rule matches.

## Prepared Snapshot Rows

Exporters must not derive labels directly from raw NATS subjects. They consume
prepared `labeled_metrics` rows that have already passed the policy and
cardinality checks:

```json
{
  "labeled_metrics": [
    {
      "kind": "counter",
      "name": "messages_written_total",
      "value": 2,
      "labels": {
        "subject_family": "certification_orders"
      }
    }
  ]
}
```

This row says that two messages contributed to the reviewed
`certification_orders` family. It does not expose
`certification.orders.created`, `certification.orders.updated`, message IDs, or
payload content.

## Example Output

Prometheus renders the prepared row as a bounded label:

```text
# HELP nats_sinks_messages_written_total Messages reported durable by the destination sink.
# TYPE nats_sinks_messages_written_total counter
nats_sinks_messages_written_total 4
nats_sinks_messages_written_total{subject_family="certification_orders"} 2
```

The metrics CLI shows the same row in shell-friendly form:

```text
MESSAGES_WRITTEN_TOTAL=4
MESSAGES_WRITTEN_TOTAL_SUBJECT_FAMILY_CERTIFICATION_ORDERS=2
```

StatsD folds the label into a bounded metric-name component:

```text
nats_sinks.messages_written_total.subject_family.certification_orders:2|g
```

OCI Monitoring can include the label as a bounded dimension only when
`oci_monitoring.include_metric_labels_as_dimensions` is explicitly enabled:

```json
{"dimensions":{"deployment":"certification","subject_family":"certification_orders"}}
```

Syslog renders the label as a structured-data parameter:

```text
label_subject_family="certification_orders"
```

## Failure Handling

Subject-aware observability failures must stay in the observability plane:

- disabled policy produces no subject-family rows;
- denied subjects produce no subject-family rows;
- malformed policy fails validation;
- overflow is handled by `drop`, `aggregate_other`, or `fail_closed`;
- connector export failure is an observability incident, not a delivery
  decision.

The sink runner must still follow commit-then-ACK. A sink write success or
failure determines ACK, NAK, retry, or DLQ behavior. Subject-aware metrics do
not.

## Python Certification Helper

Maintainers and external connector authors can reuse the certification helper:

```python
from nats_sinks.testing import run_subject_observability_certification


def test_subject_observability_contract() -> None:
    report = run_subject_observability_certification()

    assert report.raw_subject_leaks == ()
    assert report.malformed_policy_rejected is True
    assert report.delivery_probe_before == report.delivery_probe_after
```

Use this helper for connector changes that render prepared `labeled_metrics`
rows. Connector-specific tests should add their own output checks, but they
should not weaken the shared certification invariants.

## Operator Checklist

Before enabling subject-aware export:

1. Confirm that aggregate-only metrics are insufficient.
2. Document the approved subject families and why they may be shared.
3. Choose stable labels that do not reveal sensitive operational meaning.
4. Keep `allow_raw_subjects=false` unless a formal review approves otherwise.
5. Set `max_subject_families` to the smallest useful value.
6. Choose an overflow action that operators understand.
7. Run the focused certification tests and the full repository check.
8. Review the rendered output before connecting it to a monitoring platform.
9. Keep the observability service separate from the delivery worker where
   practical.
10. Re-review the policy whenever subjects, routes, or monitoring audiences
    change.
