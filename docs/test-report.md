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
| Report generated | 2026-05-26 issue `#136` validation for upcoming `v0.4.2` development |
| Project version | `0.4.1` package metadata with `v0.4.2` development changes |
| Python version | 3.12.4 |
| Git revision checked | Branch `issue-136-named-multi-sink-config` based on `release-v0.4.2` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |

This refresh covered named multi-sink instance configuration for issue `#136`
and a full local regression cycle for the current development branch. The new
registry lets one JSON file declare several named Oracle Database, Oracle
MySQL, file, or spool sink instances, validates route target references,
extends CLI redacted review and named health checks, and preserves the current
single active `sink` runtime path.

```mermaid
flowchart LR
    Env[NatsEnvelope] --> Policy[Routing match policy]
    Policy --> Targets[Logical target names]
    Targets --> Registry[Named sink registry]
    Registry --> Future[Future fan-out delivery]
    Tests[Unit and CLI validation tests] --> Report[Sanitized latest report]
    Docs[Documentation builds] --> Report
```

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `220 files already formatted` |
| Ruff lint | Pass |
| Mypy | Pass, no issues in `87` source files |
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
| Named sink focused tests | `python -m pytest tests/unit/test_named_sinks.py tests/unit/test_cli.py tests/unit/test_config.py tests/unit/test_routing_policy.py -q` | Pass, `110 passed` |
| Main repository test suite | `scripts/check.sh` | Pass, `963 passed, 10 skipped` |
| Encryption and sink contract subset | `scripts/check.sh` | Pass, `123 passed` |
| Sink capability subset | `scripts/check.sh` | Pass, `105 passed` |
| Documentation builds | `scripts/check.sh` | Pass for Read the Docs and GitHub Pages MkDocs builds |
| Example validation | `nats-sink validate examples/named-multi-sink/config.json` through unit/CLI coverage | Pass |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, and Oracle MySQL integration tests. Issue `#136` changes validated
configuration loading, CLI validation, route reference checks, and local file
sink health checks, but it does not alter live single-sink delivery code, so no
new credentialed live test was required for this specific feature.

## Named Sink Registry Evidence

The new unit coverage verifies:

- mixed named Oracle Database and file sink configurations;
- multiple Oracle named instances for different tables and backends;
- multiple file destinations, including compressed file output configuration;
- route target validation against the named registry;
- automatic ACK-gating sink type enrichment from named sink definitions;
- route type mismatch rejection;
- duplicate named sink JSON key rejection;
- redaction of named sink secrets without hiding route target names;
- CLI route target reporting;
- CLI sink-specific validation for named sinks;
- `test-sink --sink-name` and `test-sink --all-named-sinks` for local file
  health checks.

## Issues Found During Validation

No new bugs were found during issue `#136` validation. Focused tests and the
full `scripts/check.sh` cycle completed successfully.

## Documentation Evidence

The following public documentation was updated and built successfully:

- [README](https://github.com/ProjectCuillin/nats-sinks/blob/main/README.md)
- [Configuration](configuration.md)
- [Sink Framework](sink-framework.md)
- [Named Sinks And Routing](named-sinks.md)
- [Architecture](architecture.md)
- [Operations](operations.md)
- [Commit Then ACK](commit-then-ack.md)
- [Idempotency](idempotency.md)
- [Security](security.md)
- [File Sink](file-sink.md)
- [Oracle Sink](oracle-sink.md)
- [Named Multi-Sink Example](https://github.com/ProjectCuillin/nats-sinks/blob/main/examples/named-multi-sink/config.json)
- [Documentation Home](index.md)

The changelog, backlog metadata, public API compatibility tests, CLI validation
test, and tracked named multi-sink example were also updated for issue `#136`.
