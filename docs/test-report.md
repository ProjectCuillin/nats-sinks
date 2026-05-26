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
| Report generated | 2026-05-26 issue `#126` validation for upcoming `v0.4.2` development |
| Project version | `0.4.1` package metadata with `v0.4.2` development changes |
| Python version | 3.12.4 |
| Git revision checked | Branch `issue-126-subject-aware-metric-aggregation` based on `release-v0.4.2` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |

This refresh covered bounded subject-family metric aggregation for issue `#126`
and a full local regression cycle for the current development branch. The new
tests prove that subject-family counters are built only from reviewed
observability policy rules, that raw subjects are not exported by default, that
overflow behavior remains deterministic and low-cardinality, and that existing
aggregate metrics remain unchanged.

```mermaid
flowchart LR
    Messages[Validated envelopes] --> Policy[Subject observability policy]
    Policy --> Aggregator[Subject-family aggregation]
    Aggregator --> Snapshot[labeled_metrics snapshot rows]
    Snapshot --> Exporters[Prometheus / OTLP / StatsD / syslog / Splunk HEC]
    Snapshot --> CLI[nats-sink-metrics]
    Exporters --> Report[Sanitized latest report]
    Docs[Documentation builds] --> Report
```

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `228 files already formatted` |
| Ruff lint | Pass |
| Mypy | Pass, no issues in `91` source files |
| Version metadata consistency | Pass for `0.4.1` |
| Dependency manifests | Pass, manifest files up to date |
| Backlog item validation | Pass |
| Bug report validation | Pass |
| PyPI-facing Markdown links | Pass |
| Secret scan | Pass, no high-confidence secret material found |
| Bandit | Pass with reviewed `nosec` annotations for validated SQL identifier builders |
| Package build | Pass, sdist and wheel built |
| SBOM generation | Pass, CycloneDX JSON and XML generated |
| Checksum generation | Pass, `dist/SHA256SUMS` generated |
| Twine metadata check | Pass for retained distributions |

## Test Results

| Test Area | Command | Result |
| --- | --- | --- |
| Subject-family observability focused tests | `python -m pytest tests/unit/test_subject_family_observability.py tests/unit/test_metrics.py tests/unit/test_metrics_cli.py tests/unit/test_observability_policy.py tests/unit/test_observability_cli.py tests/unit/test_prometheus_observability.py tests/unit/test_otlp_observability.py tests/unit/test_elastic_observability.py tests/unit/test_grafana_alloy_observability.py tests/unit/test_splunk_hec_observability.py tests/unit/test_statsd_observability.py tests/unit/test_syslog_observability.py tests/unit/test_public_api.py -q` | Pass, `175 passed` |
| Main repository test suite | `scripts/check.sh` | Pass, `1013 passed, 10 skipped` |
| Encryption and sink contract subset | `scripts/check.sh` | Pass, `123 passed` |
| Sink capability subset | `scripts/check.sh` | Pass, `117 passed` |
| Documentation builds | `scripts/check.sh` | Pass for Read the Docs and GitHub Pages MkDocs builds |
| Example validation | `nats-sink validate examples/named-multi-sink/config.json` through unit/CLI coverage | Pass |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, and Oracle MySQL integration tests. Issue `#126` adds bounded
observability aggregation only. It does not change message delivery, ACK
behavior, retries, DLQ behavior, sink writes, or idempotency behavior.

## Subject-Family Aggregation Evidence

The new unit coverage verifies:

- aggregate metrics remain available and unchanged;
- subject-family metrics are emitted only for approved policy rules;
- raw subjects are not exported by default;
- denied subjects do not create metric rows;
- overflow can aggregate to a reviewed fallback label, drop the overflowed
  rows, or fail closed;
- snapshot rows are bounded, typed, and validated before rendering;
- Prometheus, OTLP, StatsD, syslog, Splunk HEC, and `nats-sink-metrics` consume
  prepared `labeled_metrics` rows instead of deriving labels from raw subjects;
- metric labels are restricted to stable operator-approved labels such as
  `subject_family`;
- subject-family aggregation remains observational only and does not affect
  delivery semantics.

## Issues Found During Validation

No new product bugs were found during issue `#126` validation.

## Documentation Evidence

The following public documentation was updated and built successfully:

- [README](https://github.com/ProjectCuillin/nats-sinks/blob/main/README.md)
- [Configuration](configuration.md)
- [Sink Framework](sink-framework.md)
- [Sink Certification](sink-certification.md)
- [Testing](testing.md)
- [Development](development.md)
- [Architecture](architecture.md)
- [Operations](operations.md)
- [Metrics](metrics.md)
- [Observability](observability.md)
- [Subject-Aware Observability Evaluation](subject-aware-observability-evaluation.md)
- [Prometheus Integration](prometheus.md)
- [Named Sinks And Routing](named-sinks.md)
- [Idempotency](idempotency.md)
- [Security](security.md)
- [File Sink](file-sink.md)
- [Oracle Sink](oracle-sink.md)
- [Named Multi-Sink Example](https://github.com/ProjectCuillin/nats-sinks/blob/main/examples/named-multi-sink/config.json)
- [Documentation Home](index.md)

The changelog, backlog metadata, public API contract tests, metrics CLI tests,
observability connector tests, and subject-aware observability documentation
were also updated for issue `#126`.
