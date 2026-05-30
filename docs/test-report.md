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
| Report generated | 2026-05-30 HTTP sink NGINX FIPS test endpoint validation for `v0.4.3` |
| Project version | `0.4.2` development tree for the next release |
| Python version | 3.12.4 |
| Git revision checked | Branch `issue-http-nginx-fips-test-container` with bug-fix branches `#345`, `#346`, and `#347`, to be merged back into `release-v0.4.3` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Live-gated disconnected replay certification passed with local operator-provided Oracle integration settings; no connection details are recorded here |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle NoSQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Coherence details | Environment-gated live tests skipped unless explicitly enabled |
| Live S3-compatible object storage details | Environment-gated live tests skipped unless explicitly enabled |
| Container e2e details | HTTP sink NGINX FIPS endpoint, Oracle MySQL Database, Oracle NoSQL Database, and Oracle Coherence Community Edition container-backed checks passed locally |

This refresh covers issue `#344`, which adds a local-only Oracle Linux 9 slim
FIPS based NGINX endpoint for HTTP sink e2e validation. The validation builds
the test image, runs it with loopback-only exposure and hardened Docker flags,
sends fake events through the production `HttpSink`, and verifies captured
request evidence for the HTTP envelope, subject, payload marker, route header,
and idempotency key.

Three managed bugs were found during validation and handled through the agreed
bug workflow:

- bug `#345` covered NGINX runtime directories missing after tmpfs mounts;
- bug `#346` covered the HTTP e2e runner passing an unsupported helper
  argument;
- bug `#347` covered request evidence transfer from the container.

All three bugs were reproduced, fixed with focused regressions, and included in
this report. Their GitHub issues carry sanitized failing-test evidence and are
ready for completion comments after the feature branch is merged.

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `297` files already formatted |
| Ruff lint | Pass |
| Mypy | Pass, no issues in `127` source files |
| Version metadata consistency | Pass for `0.4.2` development metadata |
| Dependency manifests | Pass, manifest files up to date |
| Backlog metadata | Pass, `155` backlog items validated |
| Bug report metadata | Pass, `101` bug reports validated |
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
| Main repository test suite | `scripts/check.sh` | Pass, `1332 passed, 17 skipped` |
| Commit, encryption, file, and Oracle sink subset | run by `scripts/check.sh` | Pass, `142 passed` |
| Sink certification and example validation | `scripts/check-sinks.sh` through `scripts/check.sh` | Pass, `235 passed` plus configuration validation for file, Oracle Database, Oracle MySQL Database, Oracle NoSQL Database, Oracle Coherence Community Edition, fan-out, Foundry, Gotham, HTTP, and S3 examples |
| Focused HTTP sink NGINX container e2e | `python scripts/run-http-sink-nginx-e2e.py --timeout-seconds 120` | Pass; the Oracle Linux 9 slim FIPS NGINX endpoint accepted fake HTTP sink events and exposed sanitized request evidence |
| Container-backed sink e2e | `python scripts/run-container-e2e-suite.py --timeout-seconds 300` | Pass; HTTP sink NGINX FIPS endpoint, Oracle MySQL Database, Oracle NoSQL Database, and Oracle Coherence Community Edition container-backed checks passed |
| Oracle Database disconnected replay | `NATS_SINKS_ORACLE_DISCONNECTED_REPLAY=1 python -m pytest -m integration tests/integration/test_oracle_sink.py -q -k disconnected` | Pass, `1 passed, 4 deselected` |
| Focused disconnected replay unit coverage | `python -m pytest tests/unit/test_disconnected_spool_replay.py tests/unit/test_oracle_disconnected_replay_verification.py -q` | Pass, `3 passed` |
| Focused HTTP container unit coverage | `python -m pytest tests/unit/test_http_nginx_test_container.py -q` | Pass, `15 passed` |
| Focused static checks | `python -m ruff check ...` and `python -m ruff format --check ...` through `scripts/check.sh` | Pass |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, Oracle MySQL, Oracle NoSQL Database, Oracle Coherence Community
Edition, S3-compatible object storage, and push-consumer integration tests.
They require explicit operator-provided services or environment flags and were
not implicitly run by the default release-preparation check.

The Oracle Coherence container-backed e2e run emitted two Python 3.14 protobuf
deprecation warnings from the optional Coherence client dependency. The test
passed and no project code defect was found.

## HTTP NGINX Container Evidence

| Component | Evidence |
| --- | --- |
| Dockerfile | Uses `container-registry.oracle.com/os/oraclelinux:9-slim-fips`, installs NGINX and Python, runs as UID/GID `10001`, and exposes only port `8080` inside the container |
| NGINX configuration | Serves `/health`, proxies `/nats-sink` to a loopback capture helper, returns `404` for other paths, and places all temp paths under tmpfs-backed `/tmp/nginx` |
| Runner hardening | Uses fixed subprocess argument lists, loopback port binding, read-only root, dropped Linux capabilities, `no-new-privileges`, tmpfs-backed writable paths, and cleanup by default |
| HTTP sink verification | Sends fake events through the production `HttpSink`, then validates request method, path, schema, subject, payload marker, route header, and idempotency key |
| Full container suite | Runs the HTTP endpoint together with Oracle MySQL Database, Oracle NoSQL Database, and Oracle Coherence Community Edition container-backed e2e checks |

All payloads are synthetic. No live subjects, credentials, generated database
passwords, private endpoints, wallet paths, certificate material, or container
identifiers are retained in this report.

## Issues Found During Validation

Managed bug `#345` was found during the first live HTTP endpoint run:

- the failing evidence showed NGINX could not create tmpfs-backed runtime
  directories under the read-only-root run policy;
- the entrypoint now recreates all runtime directories before starting NGINX;
- NGINX temp paths now point under `/tmp/nginx`;
- the focused regression and live HTTP endpoint e2e test passed after the fix.

Managed bug `#346` was found after the endpoint started:

- the failing evidence showed the runner passed an unsupported
  `consumer_sequence` keyword to the shared `certification_envelope` helper;
- the runner now uses only supported helper arguments;
- a focused unit test exercises message construction through a fake `HttpSink`;
- the focused regression and live HTTP endpoint e2e test passed after the fix.

Managed bug `#347` was found during request evidence transfer:

- the failing evidence showed direct file copy from the container could miss a
  tmpfs-backed request evidence file on the local Docker engine;
- the runner now uses bounded, shell-free `docker exec cat` evidence transfer
  with a short retry loop;
- a focused unit test covers transient evidence-readiness behavior;
- the focused regression and live HTTP endpoint e2e test passed after the fix.

No unresolved code, packaging, documentation, sink, or container-backed e2e
defects remain from this validation pass.

## Documentation Evidence

The following release-facing documentation was updated or validated:

- [Disconnected Spool Replay Testing](disconnected-spool-replay-testing.md)
- [Testing](testing.md)
- [HTTP Sink NGINX FIPS Test Endpoint](http-nginx-test-container.md)
- [HTTP Sink](http-sink.md)
- [Local Docker Stack](docker.md)
- [Edge Spool Sink](spool-sink.md)
- [Documentation Home](index.md)
- [Configuration](configuration.md)
- [Oracle MySQL Sink](mysql-sink.md)
- [Oracle NoSQL Database Sink](oracle-nosql-sink.md)
- [Oracle Coherence Community Edition Sink](coherence-sink.md)

The changelog, backlog metadata, bug metadata, latest test report, package
artifacts, SBOM, and checksum evidence were refreshed or validated for this
`v0.4.3` development branch.
