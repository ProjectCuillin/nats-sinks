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
| Report generated | 2026-05-26 issue `#138` validation for upcoming `v0.4.2` development |
| Project version | `0.4.1` package metadata with `v0.4.2` development changes |
| Python version | 3.12.4 |
| Git revision checked | Branch `issue-138-routing-match-policy` based on `release-v0.4.2` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |

This refresh covered the generic route-match policy selector for issue `#138`
and a full local regression cycle for the current development branch. The new
policy validates subject, priority, classification, label, and approved header
matching without changing sink delivery or JetStream ACK behavior.

```mermaid
flowchart LR
    Env[NatsEnvelope] --> Policy[Routing match policy]
    Policy --> Targets[Logical target names]
    Targets --> Future[Future fan-out delivery]
    Tests[Unit and CLI validation tests] --> Report[Sanitized latest report]
    Docs[Documentation builds] --> Report
```

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `217 files already formatted` |
| Ruff lint | Pass |
| Mypy | Pass, no issues in `86` source files |
| Version metadata consistency | Pass for `0.4.1` |
| Dependency manifests | Pass, manifest files up to date |
| Backlog item validation | Pass, `142` backlog items validated |
| Bug report validation | Pass, `87` bug report items validated |
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
| Route policy focused tests | `python -m pytest tests/unit/test_routing_policy.py tests/unit/test_cli.py::test_cli_validates_routing_match_policy_example tests/unit/test_public_api.py -q` | Pass, `28 passed` |
| Config, route policy, CLI, and public API subset | `python -m pytest tests/unit/test_config.py tests/unit/test_routing_policy.py tests/unit/test_cli.py tests/unit/test_public_api.py -q` | Pass, `90 passed` |
| Main repository test suite | `scripts/check.sh` | Pass, `932 passed, 10 skipped` |
| Encryption and sink contract subset | `scripts/check.sh` | Pass, `123 passed` |
| Sink capability subset | `scripts/check.sh` | Pass, `105 passed` |
| Documentation builds | `scripts/check.sh` | Pass for Read the Docs and GitHub Pages MkDocs builds |
| Example validation | `nats-sink validate examples/routing-match-policy/config.json` through unit/CLI coverage | Pass |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, and Oracle MySQL integration tests. Issue `#138` changes only the
validated route selector and does not alter live sink delivery code, so no new
live credentialed test was required for this specific feature.

## Route-Match Policy Evidence

The new unit coverage verifies:

- subject-only, priority-only, classification-only, `labels_all`,
  `labels_any`, `labels_none`, header-only, and combined route matches;
- missing metadata, empty labels, repeated labels, absent headers, disabled
  routing, no-match `reject`, `ignore`, and `default_route` behavior;
- `mode: "first"` and `mode: "all"` selection, including target
  de-duplication in policy order;
- fail-closed configuration validation for malformed subject patterns, unknown
  match operators, excessive value counts, empty match objects, ambiguous
  default-route settings, and secret-bearing header names;
- the documented NATO SECRET and NATO UNCLASS example routes.

## Issues Found During Validation

No new bugs were found during issue `#138` validation. The only early failure
was formatting-only and was corrected before rerunning `scripts/check.sh`.

## Documentation Evidence

The following public documentation was updated and built successfully:

- [README](https://github.com/ProjectCuillin/nats-sinks/blob/main/README.md)
- [Configuration](configuration.md)
- [Sink Framework](sink-framework.md)
- [Architecture](architecture.md)
- [Operations](operations.md)
- [File Sink](file-sink.md)
- [Oracle Sink](oracle-sink.md)
- [Documentation Home](index.md)

The changelog, backlog metadata, public API compatibility tests, CLI validation
test, and tracked route policy example were also updated for issue `#138`.
