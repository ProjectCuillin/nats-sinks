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
| Report generated | 2026-05-29 release validation and blocker fix for `v0.4.2` |
| Project version | `0.4.2` |
| Python version | 3.12.4 |
| Git revision checked | Branch `bugfix-release-ci-latest-ruff-lint`, to be merged back into `release-v0.4.2` |
| Live NATS details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Database details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle MySQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle NoSQL details | Environment-gated live tests skipped unless explicitly enabled |
| Live Oracle Coherence details | Environment-gated live tests skipped unless explicitly enabled |
| Live S3-compatible object storage details | Environment-gated live tests skipped unless explicitly enabled |
| Container e2e details | Oracle NoSQL Database and Oracle Coherence Community Edition container-backed sink e2e passed locally |

This refresh prepares the `v0.4.2` release branch after the HTTP, S3, Oracle
NoSQL Database, Oracle Coherence Community Edition, Palantir Foundry, Palantir
Gotham, OCI Monitoring, routing, documentation, and release-preparation changes
planned for the release. Two release-preparation bugs were found during the
release cycle. Managed bug `#328` covered a GitHub CLI authentication helper
false negative. Managed bug `#331` covered a hosted release-validation CI lint
failure caused by newer Ruff rules than the first local release-preparation
environment used. Both were reproduced with deterministic tests, fixed,
commented on their issues, marked `completed`, and included in this report.

## Core And Repository Validation

| Check | Result |
| --- | --- |
| Ruff format | Pass, `294` files already formatted |
| Ruff lint | Pass, including a local check with Ruff `0.15.15` to match the hosted release-validation runner |
| Mypy | Pass, no issues in `126` source files |
| Version metadata consistency | Pass for `0.4.2` |
| Dependency manifests | Pass, manifest files up to date |
| Backlog metadata | Pass, `148` backlog items validated |
| Bug report metadata | Pass, `96` bug reports validated |
| PyPI-facing Markdown links | Pass |
| Documentation builds | Pass for Read the Docs and GitHub Pages MkDocs builds |
| Security checks | Pass; existing reviewed `nosec` warnings remained non-blocking |
| Package build | Pass, source distribution and wheel built for `0.4.2` |
| SBOM and checksums | Pass, CycloneDX JSON/XML and checksum manifest generated for `0.4.2` |
| GitHub CLI release helper | Pass after bug `#328`; authenticated API probing is used without printing token values |
| Release CI lint compatibility | Pass after bug `#331`; stale Ruff suppressions and async pathlib use were covered by a focused regression |

The documentation build emitted the existing upstream Material for MkDocs
warning about MkDocs 2.0. The repository checks remained passing.

## Test Results

| Test Area | Command | Result |
| --- | --- | --- |
| Main repository test suite | `scripts/check.sh` | Pass, `1314 passed, 13 skipped` |
| Commit, encryption, file, and Oracle sink subset | run by `scripts/check.sh` | Pass, `142 passed` |
| Sink certification and example validation | `scripts/check-sinks.sh` through `scripts/check.sh` | Pass, `217 passed` plus configuration validation for file, Oracle Database, Oracle MySQL Database, Oracle NoSQL Database, Oracle Coherence Community Edition, fan-out, Foundry, Gotham, HTTP, and S3 examples |
| Container-backed sink e2e | `NATS_SINKS_RUN_CONTAINER_E2E=1 scripts/check-sinks.sh` | Pass; Oracle NoSQL Database and Oracle Coherence Community Edition container-backed sink e2e passed |
| GitHub auth helper regression | `python -m pytest tests/unit/test_check_gh_auth_script.py -q` | Pass, `2 passed` |
| GitHub auth helper live check | `scripts/check-gh-auth.sh --check-only` | Pass with authenticated API access |
| Release CI lint compatibility regression | `python -m pytest tests/unit/test_release_ci_lint_compatibility.py -q` | Pass, `2 passed` |
| Latest Ruff compatibility check | `python -m ruff check .` with Ruff `0.15.15` | Pass |
| Full local validation | `scripts/check.sh` | Pass |

The skipped tests are the existing environment-gated live NATS, Oracle
Database, Oracle MySQL, Oracle NoSQL Database, Oracle Coherence Community
Edition, S3-compatible object storage, and push-consumer integration tests.
They require explicit operator-provided services or environment flags and were
not implicitly run by the default release-preparation check.

The Oracle Coherence container-backed e2e run emitted two Python 3.14 protobuf
deprecation warnings from the optional Coherence client dependency. The test
passed and no project code defect was found.

## Release Readiness Evidence

| Check | Result |
| --- | --- |
| Open release-labeled issues | `52` open issues with `release-v0.4.2` were checked after bug `#331`; all completed development work is waiting for release-gated closure |
| Release-prep bugs found during validation | Bugs `#328` and `#331` created, reproduced, fixed, documented, and marked `completed` for `v0.4.2` |
| Open PRs into `release-v0.4.2` | None before this release-preparation branch is opened |
| Open PRs into `main` | None before this release-preparation branch is opened |
| Dist artifacts | `nats_sinks-0.4.2.tar.gz` and `nats_sinks-0.4.2-py3-none-any.whl` built successfully |
| SBOM artifacts | `nats-sinks-0.4.2.cyclonedx.json` and `nats-sinks-0.4.2.cyclonedx.xml` generated |
| Checksum manifest | `dist/SHA256SUMS` generated and verified for tracked release artifacts |

Feature requests and managed bug reports remain open until the GitHub Release
for the associated release is published. The `completed` label means the work
is done in development and waiting for release-gated closure.

## Issues Found During Validation

Managed bug `#328` was found during release preparation:

- the failing regression showed `scripts/check-gh-auth.sh --check-only` could
  reject a usable GitHub CLI environment when the quiet status path was
  unreliable;
- the helper now performs a silent authenticated API probe instead of relying
  on the quiet status path;
- the regression test and live helper check both passed after the fix;
- the issue has failing-test evidence, completion evidence, checked acceptance
  criteria, and the `completed` label.

Managed bug `#331` was found when the release-validation CI workflow installed
a newer Ruff version than the first local release-preparation environment:

- the failing CI run reported stale subprocess lint suppressions in local
  container e2e helper scripts and a direct pathlib call inside an async
  multi-sink routing helper;
- a focused static regression now covers both compatibility expectations;
- the subprocess helpers continue to use fixed argument lists, no shell, bounded
  timeouts, and explicit environments;
- the multi-sink routing helper delegates local directory creation outside the
  async event loop;
- the focused regression, full local validation, latest Ruff compatibility
  check, and container-backed sink e2e suite all passed after the fix.

No unresolved code, packaging, documentation, sink, or container-backed e2e
defects remain from this validation pass.

## Documentation Evidence

The following release-facing documentation was updated or validated:

- [README](https://github.com/ProjectCuillin/nats-sinks/blob/main/README.md)
- [Documentation Home](index.md)
- [Release](release.md)
- [Publishing](publishing.md)
- [Getting Started](getting-started.md)
- [Configuration](configuration.md)
- [Testing](testing.md)
- [Security](security.md)
- [Roadmap](roadmap.md)
- [Public API](public-api.md)
- [Defence And Mission Support](use-cases/defence/index.md)

The changelog, version metadata, release helper documentation, bug metadata,
latest test report, generated dependency manifests, package artifacts, SBOM,
and checksum evidence were refreshed for `v0.4.2` release preparation.
