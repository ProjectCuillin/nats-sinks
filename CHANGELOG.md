# Changelog

All notable changes to this project will be documented in this file.

The format follows Keep a Changelog, and this project uses semantic versioning.

Repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

Named contributor: Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

## [Unreleased]

No unreleased changes yet.

## [0.3.0] - 2026-05-20

This release is the next feature release after `0.2.1`. The main themes are
safer payload handling and richer message context that is resolved once in the
core runtime and then persisted consistently by every production sink.

Highlights:

- Payload encryption can now be enabled before sink delivery. The core runner
  encrypts only the NATS message body with AES-256-GCM or AES-256-CCM, leaving
  operational metadata available for routing, idempotency, observability, and
  troubleshooting. Operators can enable one global policy for all subjects or
  ordered per-subject rules for selective encryption and exemptions.
- Every message can now carry normalized `priority`, `classification`, and
  `labels` metadata. Values can come from configurable NATS headers,
  deployment defaults, ordered subject-specific defaults, or remain null/empty
  when neither is provided.
- Oracle storage now includes dedicated `PRIORITY`, `CLASSIFICATION`, and `LABELS`
  columns in the recommended table shape.
- File sink JSON output now includes top-level `priority`, `classification`,
  `labels`, and `labels_list` fields as well as the same values in the generic
  metadata document.
- Mermaid diagrams now render from the same Markdown source for Read the Docs
  and GitHub Pages.
- Public documentation now uses more mission-oriented wording where relevant,
  with examples for defence logistics, operational reporting, sensitive
  payload handling, audit trails, DLQ triage, and disconnected handoff patterns.

Upgrade notes:

- Existing Oracle tables must be migrated before using the `0.3.0` Oracle
  default column mapping. Add nullable `PRIORITY`,
  `CLASSIFICATION`, and `LABELS` columns, or configure
  `sink.columns.priority`, `sink.columns.classification`, and
  `sink.columns.labels` to match existing columns.
- If an older retained Oracle integration or e2e test table is reused, the
  test will fail fast with a schema message. Use a fresh table or the
  documented drop-before-test flag for test-only tables.
- Payload encryption requires installing the optional crypto extra:
  `pip install "nats-sinks[crypto]"`.
- When payload encryption is enabled, prefer metadata-based idempotency such as
  JetStream stream sequence or message ID. Do not depend on plaintext payload
  fields after the core encrypts the message body.

Validation snapshot:

- Full local check script passed with `164 passed, 8 skipped`.
- Encryption-focused check passed with `68 passed`.
- Sink capability check passed with `66 passed`.
- Live NATS-to-Oracle e2e passed for both unencrypted and encrypted modes
  against fresh retained test tables that include the new `LABELS` column.
  The runs verified priority/classification/labels persistence, encrypted
  payload storage, decrypt verification, and commit-then-ACK completion.

### Added

- Added optional core payload encryption before sink delivery, with
  AES-256-GCM and AES-256-CCM support through the `nats-sinks[crypto]` extra.
- Added encrypted payload envelope helpers and public Python imports for
  `EncryptionConfig`, `EncryptionRuleConfig`, `PayloadEncryptor`,
  `SubjectPayloadEncryptor`, and `decrypt_payload`.
- Added subject-specific payload encryption rules with NATS wildcard matching,
  first-match-wins behavior, disabled-rule exemptions, inherited global
  encryption settings, and dedicated unit plus local file e2e coverage.
- Added encryption coverage for core runner ordering, file sink storage,
  gzip-plus-encryption file output, Oracle row mapping, local file e2e, and
  live Oracle e2e opt-in mode.
- Added `scripts/check-encryption.sh` for temporary test key generation and
  encryption-focused validation, with a `--preserve-key-material` debug flag.
- Added core-normalized `priority`, `classification`, and `labels` message
  metadata fields with configurable NATS header extraction, defaults, file sink
  persistence, Oracle columns, and unit/e2e coverage across present and missing
  values.
- Added subject-specific priority, classification, and labels defaults under
  `message_metadata.rules`, using NATS wildcard matching and first-match-wins
  resolution while preserving header values as authoritative.
- Added encrypted file sink example configuration under
  `examples/payload-encryption/`.
- Added `scripts/check-gh-auth.sh` so maintainers can validate local GitHub CLI
  authentication, and optionally start interactive browser login, before
  pushing release tags.
- Documented the GitHub CLI authentication preflight in the release and
  publishing runbooks.
- Documented payload encryption configuration, subject-specific encryption
  rules, encrypted envelope shape, decryption helpers, key handling,
  idempotency guidance, and encrypted Oracle/file sink behavior.
- Documented priority/classification/labels message metadata configuration,
  subject-specific defaults, semicolon-separated label storage, null/empty
  handling, Oracle schema impact, file sink output shape, and test coverage.
- Added concrete documentation examples showing how encrypted payloads,
  NATO-style classification values, priority, and semicolon-separated labels
  appear in file sink JSON records and Oracle table rows.
- Added public PyPI and supported-Python-version badges to the README and
  documentation home page, with publishing guidance for future badge updates.
- Expanded `ROBOTS.md` and `AGENTS.md` with payload-encryption,
  priority/classification/labels metadata, live Oracle e2e, retained
  test-table, and Oracle JSON-column handling guidance for future maintainers
  and AI agents.

### Changed

- Extended the recommended Oracle table DDL with nullable `PRIORITY`,
  `CLASSIFICATION`, and `LABELS` columns.
- Extended the generic metadata snapshot with a `message_metadata` object that
  contains normalized `priority`, `classification`, and `labels` values.
- Extended file sink output records with top-level `priority` and
  `classification`, `labels`, and `labels_list` fields.
- Updated the example file and Oracle configurations to show the new
  `message_metadata` section.
- Updated the sanitized test report with the latest local and live e2e
  validation results.
- Refined README and documentation wording so public readers in operational,
  public-sector, and defence-adjacent environments can more easily map the
  generic sink framework to mission event streams and secure data-handling
  practices.
- Updated example JSON configurations to demonstrate NATO-style classification
  strings, priority defaults, and labels alongside encryption and sink storage
  behavior.

### Fixed

- Enabled Mermaid fenced-code rendering in MkDocs so Read the Docs and GitHub
  Pages can render diagrams from the same Markdown source.

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
