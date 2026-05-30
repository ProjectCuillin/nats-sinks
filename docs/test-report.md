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
| Report generated | 2026-05-30 disconnected backend spool replay certification for `v0.4.3` |
| Project version | `0.4.2` development tree for the next release |
| Python version | 3.12.4 |
| Git revision checked | Branch `issue-334-disconnected-spool-replay-e2e`, to be merged back into `release-v0.4.3` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Live-gated disconnected replay certification passed with local operator-provided Oracle integration settings; no connection details are recorded here |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle NoSQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Coherence details | Environment-gated live tests skipped unless explicitly enabled |
| Live S3-compatible object storage details | Environment-gated live tests skipped unless explicitly enabled |
| Container e2e details | Oracle MySQL Database, Oracle NoSQL Database, and Oracle Coherence Community Edition container-backed disconnected replay checks passed locally |

This refresh covers issue `#334`, which adds disconnected backend
spool-and-replay certification for the Oracle-family sink set. The certification
uses the `1001 + 1001 + 1001` pattern: direct backend writes before an outage,
encrypted local spool custody while the backend is unavailable, replay after
recovery, direct writes after recovery, and final backend verification for
`3003` unique records.

Two managed bugs were found during validation and handled through the agreed
bug workflow:

- bug `#335` covered Oracle Database final verification using a drop-capable
  setup helper before counting rows;
- bug `#336` covered the new Oracle verification regression relying on
  `tests/` being an importable package when the standard sink script collected
  the suite.

Both bugs were reproduced, fixed with focused regressions, commented on their
issues, marked `completed`, and included in this report.

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `297` files already formatted |
| Ruff lint | Pass |
| Mypy | Pass, no issues in `127` source files |
| Version metadata consistency | Pass for `0.4.2` development metadata |
| Dependency manifests | Pass, manifest files up to date |
| Backlog metadata | Pass, `150` backlog items validated |
| Bug report metadata | Pass, `98` bug reports validated |
| PyPI-facing Markdown links | Pass |
| Documentation builds | Pass for Read the Docs and GitHub Pages MkDocs builds |
| Security checks | Pass; existing reviewed `nosec` warnings remained non-blocking |
| Package build | Pass, source distribution and wheel built from the current tree |
| SBOM and checksums | Pass, CycloneDX JSON/XML and checksum manifest generated |

The documentation build emitted the existing upstream Material for MkDocs
warning about MkDocs 2.0. The repository checks remained passing.

## Test Results

| Test Area | Command | Result |
| --- | --- | --- |
| Main repository test suite | `scripts/check.sh` | Pass, `1317 passed, 17 skipped` |
| Commit, encryption, file, and Oracle sink subset | run by `scripts/check.sh` | Pass, `142 passed` |
| Sink certification and example validation | `scripts/check-sinks.sh` through `scripts/check.sh` | Pass, `220 passed` plus configuration validation for file, Oracle Database, Oracle MySQL Database, Oracle NoSQL Database, Oracle Coherence Community Edition, fan-out, Foundry, Gotham, HTTP, and S3 examples |
| Container-backed sink e2e | `NATS_SINKS_RUN_CONTAINER_E2E=1 scripts/check-sinks.sh` | Pass; Oracle MySQL Database, Oracle NoSQL Database, and Oracle Coherence Community Edition container-backed disconnected replay checks passed |
| Oracle Database disconnected replay | `NATS_SINKS_ORACLE_DISCONNECTED_REPLAY=1 python -m pytest -m integration tests/integration/test_oracle_sink.py -q -k disconnected` | Pass, `1 passed, 4 deselected` |
| Focused disconnected replay unit coverage | `python -m pytest tests/unit/test_disconnected_spool_replay.py tests/unit/test_oracle_disconnected_replay_verification.py -q` | Pass, `3 passed` |
| Focused static checks | `python -m ruff check ...` and `python -m ruff format --check ...` for changed source, tests, and runners | Pass |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, Oracle MySQL, Oracle NoSQL Database, Oracle Coherence Community
Edition, S3-compatible object storage, and push-consumer integration tests.
They require explicit operator-provided services or environment flags and were
not implicitly run by the default release-preparation check.

The Oracle Coherence container-backed e2e run emitted two Python 3.14 protobuf
deprecation warnings from the optional Coherence client dependency. The test
passed and no project code defect was found.

## Disconnected Replay Evidence

| Backend | Evidence |
| --- | --- |
| Deterministic fake backend | Unit test proves `1001` direct-before, `1001` spooled, `1001` direct-after records, empty spool after replay, and outage detection |
| Oracle Database | Live-gated certification passed with the drop-before-test setup flag enabled and final non-destructive verification |
| Oracle MySQL Database | Local short-lived container e2e passed and verified the disconnected replay count pattern |
| Oracle NoSQL Database | Local KVLite container e2e passed and verified the disconnected replay count pattern |
| Oracle Coherence Community Edition | Local container e2e passed and verified the disconnected replay key pattern |

All payloads are synthetic. No live subjects, credentials, generated database
passwords, private endpoints, wallet paths, certificate material, or container
identifiers are retained in this report.

## Issues Found During Validation

Managed bug `#335` was found during the live Oracle Database disconnected
replay run:

- the failing regression proved final verification could call the destructive
  integration setup helper;
- the verification path now opens a dedicated non-destructive verification
  connection and stops it directly;
- the focused regression and live Oracle Database disconnected replay
  certification both passed after the fix;
- the issue has failing-test evidence, completion evidence, checked acceptance
  criteria, and the `completed` label.

Managed bug `#336` was found when the standard sink suite collected the new
Oracle verification regression:

- the failing standard sink run reported a fragile `tests.integration` import;
- the regression now loads the Oracle integration adapter from the repository
  file path;
- the focused regression, static checks, and full container-backed sink gate
  passed after the fix;
- the issue has failing-test evidence, completion evidence, checked acceptance
  criteria, and the `completed` label.

No unresolved code, packaging, documentation, sink, or container-backed e2e
defects remain from this validation pass.

## Documentation Evidence

The following release-facing documentation was updated or validated:

- [Disconnected Spool Replay Testing](disconnected-spool-replay-testing.md)
- [Testing](testing.md)
- [Edge Spool Sink](spool-sink.md)
- [Documentation Home](index.md)
- [Configuration](configuration.md)
- [Oracle MySQL Sink](mysql-sink.md)
- [Oracle NoSQL Database Sink](oracle-nosql-sink.md)
- [Oracle Coherence Community Edition Sink](coherence-sink.md)

The changelog, backlog metadata, bug metadata, latest test report, package
artifacts, SBOM, and checksum evidence were refreshed or validated for this
`v0.4.3` development branch.
