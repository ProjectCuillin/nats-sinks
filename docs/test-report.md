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
| Report generated | 2026-05-28 issue `#150` Foundry sink validation for upcoming `v0.4.2` development |
| Project version | `0.4.1` package metadata with `v0.4.2` development changes |
| Python version | 3.12.4 |
| Git revision checked | Branch `issue-150-palantir-foundry-sink` based on `release-v0.4.2` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live Foundry details | No live Foundry tenant was contacted; validation used local fake clients and contract tests |

This refresh covered the experimental Palantir Foundry Streams sink for issue
`#150`. The implementation keeps Foundry support disabled unless configured,
validates stream push URLs and allowed hosts before any HTTP request is built,
requires secrets to come from environment variables, bounds request and
response sizes, retries only within explicit budgets, and treats local
fake-client certification as separate from future live Foundry certification.

```mermaid
flowchart LR
    Runner[JetStream runner] --> Envelope[NATS envelope]
    Envelope --> Mapper[Foundry record mapper]
    Mapper --> Client[Foundry stream client]
    Client --> Target[Foundry Streams push endpoint]
    Client -. local tests use .-> Fake[Fake contract client]
```

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `247 files already formatted` |
| Ruff lint | Pass |
| Mypy | Pass, no issues in `100` source files |
| Version metadata consistency | Pass for `0.4.1` |
| Dependency manifests | Pass, manifest files up to date |
| Backlog metadata | Pass, `142` backlog items validated |
| Bug report metadata | Pass, `90` bug reports validated |
| PyPI-facing Markdown links | Pass |
| Documentation builds | Pass for Read the Docs and GitHub Pages MkDocs builds |
| Security checks | Pass; existing reviewed `nosec` warnings remained non-blocking |
| Package build | Pass, source distribution and wheel built |
| SBOM and checksums | Pass, CycloneDX JSON/XML and checksum manifest generated |

## Test Results

| Test Area | Command | Result |
| --- | --- | --- |
| Foundry static regression | `python -m pytest tests/unit/test_foundry_static_security.py -q` | Pass, `1 passed` |
| Foundry focused subset | `python -m pytest tests/unit/test_foundry_static_security.py tests/unit/test_foundry_sink.py -q` | Pass, `15 passed` |
| Main repository test suite | `python -m pytest -q` | Pass, `1133 passed, 11 skipped` |
| Commit, encryption, file, and Oracle sink subset | run by `scripts/check.sh` | Pass, `130 passed` |
| Sink certification and example validation | `scripts/check-sinks.sh` via `scripts/check.sh` | Pass, `131 passed` plus file, Oracle, and Foundry config validation |
| Full local validation | `scripts/check.sh` | Pass |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, Oracle MySQL, and push-consumer integration tests.

## Foundry Evidence

The new focused coverage verifies:

- Foundry sink configuration rejects ambiguous or unsafe URLs;
- HTTPS is required outside explicit loopback-only local testing;
- endpoint allow-listing is enforced before HTTP requests are made;
- authentication uses environment variable names instead of inline token
  values;
- record field names, batch sizes, payload sizes, and response sizes are
  bounded;
- duplicate record fields and ambiguous partial acceptance fail closed;
- fake-client contract tests prove successful writes, duplicate redelivery,
  retryable failures, and permanent failures;
- runner-level tests preserve commit-then-ACK behavior for Foundry writes;
- the reviewed `urllib.request.urlopen` boundary carries the Bandit `B310`
  suppression required by the security gate.

## Issues Found During Validation

One managed bug was found and fixed during validation:

- GitHub issue `#298`: the Foundry HTTP client used a reviewed,
  config-validated `urlopen` boundary, but Bandit required the `B310`
  suppression on the exact evaluated line. A focused failing regression test was
  added first, the annotation was corrected, `scripts/security.sh` passed, and
  full `scripts/check.sh` passed afterward.

## Documentation Evidence

The following public documentation was updated and built successfully:

- [README](https://github.com/ProjectCuillin/nats-sinks/blob/main/README.md)
- [Foundry Sink](foundry-sink.md)
- [Configuration](configuration.md)
- [Sink Framework](sink-framework.md)
- [Sink Certification](sink-certification.md)
- [Security](security.md)
- [Operations](operations.md)
- [Testing](testing.md)
- [Defence Use Cases](use-cases/defence/index.md)
- [Roadmap](roadmap.md)
- [Documentation Home](index.md)

The changelog, backlog metadata, managed bug report, latest test report,
examples, and public sink documentation were updated for issue `#150`.
