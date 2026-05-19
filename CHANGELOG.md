# Changelog

All notable changes to this project will be documented in this file.

The format follows Keep a Changelog, and this project uses semantic versioning.

Repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

Named contributor: Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

## [Unreleased]

No changes yet.

## [0.2.1] - 2026-05-19

### Fixed

- Adjusted the file sink health-check unit test so it avoids direct
  `pathlib.Path.rglob()` calls inside async test code, matching Ruff's
  async-safety checks in CI.

### Added

- Added optional gzip compression for the file sink, including compressed
  multi-file test coverage and documentation.
- Added file sink e2e test controls for retaining or deleting generated local
  files, defaulting to delete-after-test behavior.
- Expanded the configuration documentation so core runtime settings, file sink
  settings, and Oracle sink settings list defaults, valid values, validation
  rules, and production guidance in one place.

### Changed

- Reordered the README and documentation home page so current production
  capabilities, including Oracle and file sinks, are introduced before future
  roadmap items.
- Added GitHub Pages documentation publishing workflow and maintainer
  documentation for enabling Pages as a hosted documentation mirror.
- Added GitHub Pages links to the README, documentation home page, release
  guide, development guide, and package project URLs.
- Added GitHub Pages MkDocs builds to local, CI, docs, and release validation
  paths so the Pages mirror is checked before future publication.
- Clarified that file sink gzip compression uses Python's standard-library
  `gzip` module and does not depend on an operating-system gzip command.

## [0.2.0] - 2026-05-18

### Added

- Added `nats_sinks.file.FileSink` as the second production sink.
- Added local file sink JSON configuration with deterministic filenames,
  atomic temporary-file placement, optional fsync, subject partitioning,
  payload normalization, metadata persistence, and duplicate policies.
- Added CLI registry support for `sink.type: "file"`.
- Added the tracked `examples/file-basic/config.json` local file sink example.
- Added unit coverage for file sink mapping, duplicate handling, path
  sanitization, payload wrapping, health checks, and filesystem error
  classification.
- Added deterministic local end-to-end coverage proving the core runner writes
  through `FileSink` before ACKing messages.
- Added `scripts/check-sinks.sh` and CI/release workflow sink capability checks
  so production sink behavior is validated before publication.
- Added Read the Docs build configuration, a GitHub Actions documentation
  workflow, and version-local documentation linking so hosted docs can build
  automatically after the one-time Read the Docs project import.
- Added dedicated file sink documentation covering configuration, durability,
  idempotency, duplicate policies, payload handling, filesystem safety,
  throughput notes, and production recommendations.

### Changed

- Updated the release workflow artifact upload action to a Node.js 24-compatible
  GitHub Action version so release jobs do not emit Node.js 20 deprecation
  warnings.
- Clarified agent and release guidance so documentation and `CHANGELOG.md` stay
  prepared for the next release throughout normal development.
- Updated README, configuration, getting started, testing, release, publishing,
  security, operations, performance, Python usage, sink framework, and roadmap
  documentation for the Oracle-plus-file-sink project shape.
- Updated package metadata for version `0.2.0` and Read the Docs project URLs.

## [0.1.1] - 2026-05-18

### Fixed

- Replaced relative Markdown documentation links with fully qualified GitHub
  URLs so the PyPI-rendered project description links to repository
  documentation correctly.
- Fixed the advertised `nats-sink --version` option so it exits successfully
  before requiring a subcommand.

### Added

- Added `scripts/check-markdown-links.py` to prevent future PyPI README link
  regressions.
- Added the Markdown link check to local check scripts and CI.
- Documented PyPI README link hygiene in the publishing runbook.
- Added a deterministic unit test for the global CLI version option.

## [0.1.0] - 2026-05-18

### Added

- Initial core JetStream sink runner with commit-then-acknowledge processing.
- Immutable `NatsEnvelope` model and sink protocol.
- Oracle sink with idempotent `merge` and `insert_ignore` modes.
- Oracle subject-to-table routing with ordered NATS wildcard patterns.
- Oracle Autonomous Database connection options for walletless TLS and
  wallet/mTLS, including wallet directory and wallet password environment
  support.
- Oracle sink sessions disable parallel DML by default to keep transactional
  multi-row batches reliable on Autonomous Database services such as `high`.
- CLI commands for run, validate, effective config, and sink testing.
- Documentation, examples, CI skeletons, and open-source governance files.
- Service deployment examples and installer scripts for Debian and Oracle Linux.
- Message sizing guidance and Oracle DDL using `CLOB` for subject storage.
- NATS token and password environment-variable support for connection secrets.
- NATS connection documentation covering token auth, username/password auth,
  server-side bcrypt password storage, and TLS with local CA certificates.
- Tracked manual live NATS probe script and example docs for connection,
  subscription, and publish-and-receive validation without committing secrets.
- Environment-gated Oracle integration tests that create the test table when
  missing, write rows, and verify duplicate redelivery is idempotent.
- Environment-gated live NATS-to-Oracle end-to-end integration test that
  publishes configurable JetStream message counts, runs `JetStreamSinkRunner`,
  stores rows in Oracle, and verifies the ACK path. The default e2e message
  count is 256.
- E2E test timing support for backend write duration through
  `batch_write_seconds`, plus wildcard subscription coverage via separate
  subscribe and publish subjects.
- Performance documentation covering Oracle write tuning, batch sizing,
  Autonomous Database behavior, and future staging-table optimization work.
- Unhappy-path hardening and deterministic fuzz-style unit coverage for message
  normalization, subject route validation, Oracle identifier validation, and
  unexpected sink exceptions.
- Shared payload normalization for JSON-capable sinks, including Oracle support
  for valid JSON, non-JSON UTF-8 text, encrypted-text-style payloads, and
  base64-wrapped bytes through `payload_mode`.
- Oracle and live NATS-to-Oracle e2e tests now cover mixed JSON and non-JSON
  text payload persistence.
- Generic NATS metadata snapshots with all headers, known and future `Nats-*`
  reserved headers, JetStream sequence metadata, and epoch nanosecond timing
  fields for message-created, received, and stored times.
- Oracle recommended schema and row mapping now include `METADATA_JSON` plus
  epoch timing columns, with integration tests covering missing `Nats-Msg-Id`
  and present `Nats-Expected-Stream` headers.
- Oracle integration and live e2e tests now use retained named test tables by
  default, with explicit opt-in `DROP_TABLE_BEFORE` and `DROP_TABLE_AFTER`
  flags plus a tracked `scripts/run-oracle-e2e.sh` helper.
- Empty NATS message bodies are covered by unit, Oracle integration, and live
  NATS-to-Oracle e2e tests.
- Added `docs/test-report.md` as the single latest sanitized validation report
  for core framework, Oracle sink, package, documentation, and live e2e checks.
- Added explicit partial-batch coverage proving that `batch_size` is an upper
  bound and that final smaller batches are written, committed, and ACKed.
- Updated `scripts/run-oracle-e2e.sh` so command-line table, message-count, and
  batch-size overrides take precedence over sourced `.local` environment files.
- Agent guidance for rigorous failure-path testing, secure coding, public code
  documentation, and graceful processing-loop behavior.
- Oracle least-privilege account documentation covering owner/runtime user
  separation, required grants, and privileges the sink service must not have.
- NATS feature gap analysis comparing current project scope with broader NATS
  connection, JetStream, stream, consumer, observability, and data abstraction
  capabilities.
- Roadmap expanded with planned NATS compatibility work and intentionally
  out-of-scope NATS capabilities.
- Release workflow now creates or updates a GitHub Release for pushed `v*`
  tags after PyPI publishing and attaches the built source distribution and
  wheel.

### Changed

- Runtime configuration uses JSON files instead of YAML files.
- Generic sink framework documentation is split from Oracle-specific documentation.
- Generic README, architecture, configuration, idempotency, message sizing, and
  performance documentation now describe the framework boundary first and link
  to Oracle-specific details from `docs/oracle-sink.md`.
- Added explicit guidance for introducing future sink modules as additive,
  non-breaking releases through the existing `NatsEnvelope`, `Sink` protocol,
  registry, optional extras, and sink-specific JSON configuration fields.
- Added a public logging level reference covering `DEBUG`, `INFO`, `WARNING`,
  `ERROR`, `CRITICAL`, runtime overrides, and payload logging guidance.
- Roadmap now tracks certified TLS certificate authentication, NKEY challenge
  authentication, and decentralized JWT authentication/authorization support.
