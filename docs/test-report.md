# Latest Test Report

This file is the canonical test report for the repository. It is intentionally
stored at a stable path and should be overwritten when a newer validation run is
performed. Do not create or commit timestamped copies of this report.

The report is sanitized. It must never contain server addresses, usernames,
passwords, tokens, certificate contents, private keys, Oracle wallet material,
full connection strings, sensitive subjects, sensitive payloads, container IDs,
generated database passwords, or full raw logs from live systems.

## Report Summary

| Field | Value |
| --- | --- |
| Overall result | Pass |
| Report generated | 2026-05-28 issue `#107` merge verification for upcoming `v0.4.2` development |
| Project version | `0.4.1` package metadata with `v0.4.2` development changes |
| Python version | 3.12.4 |
| Git revision checked | Branch `issue-107-oci-monitoring-observability` based on `release-v0.4.2` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live OCI details | No live OCI tenancy was contacted; OCI SDK behavior used local fakes |

This refresh covered the OCI Monitoring observability connector for issue
`#107` after rebasing the work on the current `release-v0.4.2` branch, which
already contained the Amazon CloudWatch observability connector. The OCI
connector is disabled by default, reads only local metrics snapshots, uses the
shared observability allow and deny policy, builds bounded OCI `PostMetricData`
requests, redacts compartment details in dry-run output, and keeps OCI export
outside JetStream delivery decisions.

```mermaid
flowchart LR
    Worker[nats-sink worker] --> Snapshot[Local metrics snapshot]
    Policy[Observability policy] --> OCI[nats-sink-observe oci-monitoring-export]
    Snapshot --> OCI
    OCI --> Monitoring[OCI Monitoring custom metrics]
    OCI -. never controls .-> Worker
```

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `240 files already formatted` |
| Ruff lint | Pass |
| Mypy | Pass, no issues in `95` source files |
| Version metadata consistency | Pass for `0.4.1` |
| Dependency manifests | Pass, manifest files up to date |
| PyPI-facing Markdown links | Pass |
| Documentation builds | Pass for Read the Docs and GitHub Pages MkDocs builds |

## Test Results

| Test Area | Command | Result |
| --- | --- | --- |
| OCI and CloudWatch focused subset | `python -m pytest tests/unit/test_oci_monitoring_observability.py tests/unit/test_cloudwatch_observability.py tests/unit/test_observability_cli.py tests/unit/test_public_api.py tests/unit/test_subject_observability_certification.py -q` | Pass, `74 passed` |
| Main repository test suite | `python -m pytest -q` | Pass, `1118 passed, 11 skipped` |
| Ruff lint | `python -m ruff check .` | Pass |
| Ruff format check | `python -m ruff format --check .` | Pass, `240 files already formatted` |
| Type checking | `python -m mypy src` | Pass, no issues in `95` source files |
| Dependency manifests | `python scripts/update-dependency-manifests.py --check` | Pass |
| PyPI-facing Markdown links | `python scripts/check-markdown-links.py` | Pass |
| Documentation builds | `scripts/check-docs.sh` | Pass for Read the Docs and GitHub Pages MkDocs builds |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, Oracle MySQL, and push-consumer integration tests.

## OCI Monitoring Evidence

The new focused coverage verifies:

- OCI Monitoring export is disabled by default and does not require a metrics
  snapshot when disabled;
- only policy-approved metric names are rendered or exported;
- deny rules win over allow rules;
- timing observations are included only when the shared policy allows them;
- default static dimensions are applied when no custom dimensions are given;
- prepared `subject_family` labels become OCI dimensions only when explicitly
  enabled;
- request splitting respects `max_metrics_per_request`;
- oversized requests fail closed through `max_request_bytes`;
- dry-run output redacts compartment OCIDs and does not print region or signer
  details;
- fake OCI clients cover success, bounded retry, timeout, and rejected-metric
  response paths without contacting a live tenancy;
- unsafe namespaces, missing enabled-region or compartment settings, sensitive
  dimensions, and empty dimension sets are rejected at policy validation time.

## Issues Found During Validation

No new release-blocking issues were found during the `#107` validation and
CloudWatch merge-conflict resolution cycle.

## Documentation Evidence

The following public documentation was updated and built successfully:

- [README](https://github.com/ProjectCuillin/nats-sinks/blob/main/README.md)
- [Observability](observability.md)
- [OCI Monitoring Integration](oci-monitoring.md)
- [Metrics](metrics.md)
- [CLI](cli.md)
- [Operations](operations.md)
- [Service Deployment](service-deployment.md)
- [Security](security.md)
- [Dependency Management](dependency-management.md)
- [Subject-Aware Observability Runbook](subject-aware-observability-runbook.md)
- [Future Observability Connectors](observability-connectors.md)
- [Roadmap](roadmap.md)
- [Documentation Home](index.md)

The changelog, backlog metadata, roadmap, latest test report, and public
observability documentation were updated for issue `#107`.
