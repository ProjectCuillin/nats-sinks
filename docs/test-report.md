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
| Report generated | 2026-05-28 issue `#103` Azure Monitor observability connector validation for upcoming `v0.4.2` development |
| Project version | `0.4.1` package metadata with `v0.4.2` development changes |
| Python version | 3.12.4 |
| Git revision checked | Branch `issue-103-azure-monitor-observability-connector` based on `release-v0.4.2` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Coherence details | Environment-gated live tests skipped unless explicitly enabled |
| Live Azure Monitor details | Not required for default validation; connector coverage used fake HTTP clients and dry-run request rendering |

This refresh covered the disabled-by-default Azure Monitor custom metrics
observability connector for issue `#103`. The connector reads only local metrics
snapshots, applies the shared observability policy, renders bounded Azure
Monitor custom metric request bodies, and keeps Azure export outside the
delivery-critical sink runner.

```mermaid
flowchart LR
    Runner[nats-sink worker] --> Snapshot[Local metrics snapshot]
    Policy[Observability policy] --> Azure[Azure Monitor connector]
    Snapshot --> Azure
    Azure --> Metrics[Azure Monitor custom metrics]
    Azure -. never controls .-> Runner
```

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `269 files already formatted` |
| Ruff lint | Pass |
| Mypy | Pass, no issues in `112` source files |
| Version metadata consistency | Pass for `0.4.1` |
| Dependency manifests | Pass, manifest files up to date |
| Backlog metadata | Pass, `145` backlog items validated |
| Bug report metadata | Pass, `90` bug reports validated |
| PyPI-facing Markdown links | Pass |
| Documentation builds | Pass for Read the Docs and GitHub Pages MkDocs builds |
| Security checks | Pass; existing reviewed `nosec` warnings remained non-blocking |
| Package build | Pass, source distribution and wheel built |
| SBOM and checksums | Pass, CycloneDX JSON/XML and checksum manifest generated |

## Test Results

| Test Area | Command | Result |
| --- | --- | --- |
| Azure Monitor focused subset | `python -m pytest tests/unit/test_azure_monitor_observability.py tests/unit/test_observability_cli.py tests/unit/test_observability_policy.py tests/unit/test_public_api.py -q` | Pass, `85 passed` |
| Full unit suite | `python -m pytest tests/unit -q` | Pass, `1213 passed` |
| Main repository test suite | run by `scripts/check.sh` | Pass, `1218 passed, 12 skipped` |
| Commit, encryption, file, and Oracle sink subset | run by `scripts/check.sh` | Pass, `130 passed` |
| Sink certification and example validation | `scripts/check-sinks.sh` via `scripts/check.sh` | Pass, `163 passed` plus file, Oracle, Oracle Coherence, multi-sink routing, Foundry, and Gotham config validation |
| Full local validation | `scripts/check.sh` | Pass |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, Oracle MySQL, Oracle Coherence, and push-consumer integration tests.

## Azure Monitor Connector Evidence

The new focused coverage verifies:

- Azure Monitor export is disabled by default and returns a safe no-op summary;
- enabled export requires explicit top-level policy enablement, Azure resource
  ID, resource location, and environment-backed bearer-token variable name;
- dry-run output renders bounded Azure Monitor custom metric request bodies
  without reading tokens or printing Azure resource IDs, locations, endpoints,
  payloads, subjects, table names, file paths, or destination addresses;
- live export uses a fake HTTP opener in tests, sends a bearer-token header
  from the configured environment variable, honors timeout settings, and
  reports only sanitized status categories;
- shared allow-list, deny-list, observation, and stale-snapshot policies are
  applied before request construction;
- static dimensions are explicit, bounded, sorted, and screened for sensitive
  or high-cardinality names and values;
- prepared metric labels stay suppressed unless
  `include_metric_labels_as_dimensions` is explicitly enabled;
- request-size limits and bounded retries fail closed with actionable errors;
- CLI output avoids printing bearer tokens, Azure resource IDs, regional
  endpoints, subjects, payloads, classification values, file paths, table
  names, or credentials.

## Issues Found During Validation

No new repository defects were found during the issue `#103` validation cycle.
The security scan reported existing reviewed `nosec` annotations as warnings,
and the check remained passing.

## Documentation Evidence

The following public documentation was updated and built successfully:

- [README](https://github.com/ProjectCuillin/nats-sinks/blob/main/README.md)
- [Azure Monitor Integration](azure-monitor.md)
- [Configuration](configuration.md)
- [CLI Reference](cli.md)
- [Metrics](metrics.md)
- [Observability](observability.md)
- [Observability Connector Roadmap](observability-connectors.md)
- [Operations](operations.md)
- [Python Usage](python-usage.md)
- [Security](security.md)
- [Documentation Home](index.md)

The changelog, backlog metadata, latest test report, and public documentation
were updated for issue `#103`.
