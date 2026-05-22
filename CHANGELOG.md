# Changelog

All notable changes to this project will be documented in this file.

The format follows Keep a Changelog, and this project uses semantic versioning.

Repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

Named contributor: Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

## [Unreleased]

### Added

- Added a quiet branch-first development and release workflow with draft pull
  request creation helpers, manual release-validation dispatch, pull request
  governance checks, CODEOWNERS review, branch protection tooling, and release
  workflow validation that tags are cut only from commits already merged into
  `main`.
- Added optional Oracle high-throughput staging-table merge mode for `merge`
  and `insert_ignore` writes, including validated staging configuration,
  staging-table DDL helpers, rollback-safe transaction handling, duplicate
  metrics support, unit coverage, and operator documentation.

### Changed

- Grouped Prometheus integration, metrics snapshot guidance, and NATS server
  monitoring under the Observability documentation section so external
  monitoring connectors are presented as sub-pages of the observability model.

### Fixed

- Fixed the high-confidence secret scanner so it prefers `rg` when available
  but falls back to `grep` in minimal CI environments where ripgrep is not
  installed.
- Updated the PyPI version badge URL to use a shorter Shields.io cache period
  so README and documentation badges refresh more quickly after releases.

## [0.4.0] - 2026-05-22

### Added

- Added a production secure-development baseline to `ROBOTS.md`, `AGENTS.md`,
  and the public security documentation, covering hostile-input handling,
  least privilege, fail-closed defaults, defense in depth, threat modeling,
  injection prevention, bounded resources, safe logging, dependency hygiene,
  file safety, deserialization safety, and testing expectations.
- Added unit coverage for log-control-character sanitization, strict log-level
  validation, duplicate JSON configuration keys, null configuration roots, and
  oversized configuration files.
- Added a dependency-free high-confidence secret scan script and wired it into
  `scripts/security.sh`, CI, and pre-commit.
- Added `docs/security-rule-review.md`, a 316-control review that maps the
  maintainer-provided secure-development guidance to the current codebase,
  test suite, documentation, non-applicable surfaces, and roadmap follow-up
  items.
- Added project-specific security controls covering documented public imports,
  release-version consistency, PyPI-safe documentation links, generated site
  output, sink capability checks, and security-register maintenance.
- Added a version-consistency check that compares `pyproject.toml`,
  `nats_sinks.__version__`, README release text, the documentation home page,
  and `CHANGELOG.md`.
- Added `scripts/check-docs.sh` so release, CI, and local documentation checks
  build Read the Docs and GitHub Pages variants in isolated temporary output
  directories instead of allowing overlapping MkDocs builds to collide in the
  shared `site/` directory.
- Added public API compatibility tests for the documented core, Oracle, and
  file sink import paths.
- Expanded public API compatibility testing into an explicit contract for
  package exports, sink extension points, documented configuration helpers, and
  `nats-sink` / `nats-sink-metrics` console-script entry points.
- Added public API compatibility documentation explaining the supported import
  surface, what the tests protect, and how future sinks should be added without
  breaking existing users.
- Added clearer basic metrics names for fetched, prepared, written, ACKed,
  NAKed, failed, DLQ, sink write, normalization error, encryption error, DLQ
  publish error, ACK error, last-success, and active-batch observations.
- Added `JsonFileMetrics`, a dependency-free local JSON metrics snapshot
  recorder that writes atomically for service scripts, local diagnostics, and
  the standalone metrics CLI.
- Added the separate `nats-sink-metrics` CLI with table, JSON, JSONL, shell,
  metric-name, and Prometheus text output, plus `show`, `get`, and `describe`
  commands for operator and developer workflows.
- Added public Python helpers for reading and flattening metrics snapshots:
  `load_metrics_snapshot`, `metric_rows_from_snapshot`, and
  `write_metrics_snapshot`.
- Added metrics contract tests and runner metrics tests proving telemetry
  increments without changing commit-then-ACK behavior.
- Added unit coverage for metrics snapshot validation, duplicate-key rejection,
  metrics CLI output formats, missing-metric handling, and stale snapshot exit
  behavior.
- Added clearer Oracle operator-facing error messages and tests for common
  schema, privilege, and authentication failures, including stale or
  incorrectly constructed retained e2e tables.
- Added NATS reconnect tuning fields, multiple seed URL support, and runner
  connection event metrics for disconnect, reconnect, close,
  discovered-server, and asynchronous error callbacks.
- Added tests proving NATS connection event metrics are recorded while
  preserving user-provided `nats-py` callbacks.
- Added release-ready documentation for NATS reconnect tuning, multiple seed
  URLs, and connection event metrics so operators can understand the supported
  configuration fields, metric names, and failure-observation behavior before
  enabling the feature in production.
- Added least-privilege NATS permission templates for sink runtime workers,
  DLQ-enabled deployments, optional runtime consumer creation, and separate
  advisory reader accounts.
- Added security, configuration, operations, DLQ, README, and roadmap links to
  the new NATS permission guidance so authorization planning is discoverable
  alongside authentication and TLS documentation.
- Added a NATS server monitoring endpoint design decision documenting that the
  delivery worker must not poll `/jsz`, `/healthz`, or other server monitoring
  endpoints, and that any future helper should be a separate
  disabled-by-default observability connector.
- Added mission-support operational example documentation for restricted event
  storage, disconnected file handoff, DLQ triage and replay preparation, and
  destination outage recovery, including configuration guidance, operational
  flow diagrams, failure behavior, sink-specific choices, and test guidance.
- Added the disabled-by-default NATS server monitoring observability connector
  under `nats-sink-observe`, with explicit endpoint allow lists, field allow
  lists, TLS verification controls, local CA support, bounded timeouts, bounded
  response size, sanitized JSON snapshots, and optional Prometheus text output
  for selected numeric values.
- Added unit tests for NATS monitoring policy validation, unsafe endpoint
  rejection, malformed JSON handling, sanitized snapshot generation, Prometheus
  rendering, and CLI behavior without making live network calls.
- Added Debian and Oracle Linux systemd assets plus installer support for the
  optional NATS monitoring snapshot service and timer, kept disabled until
  policy and service enablement are reviewed.
- Added advanced JetStream topology guidance covering mirrors, sources,
  subject transforms, republish behavior, stream compression, placement, stream
  metadata, unsupported management boundaries, and idempotency review questions.
- Added exponential, linear, and fixed retry backoff controls with optional
  full or equal jitter for delayed NAK handling after retryable failures.
- Added tests proving retryable failures use delivery-attempt-aware backoff
  delays, support deterministic no-jitter operation, and stop issuing active
  NAKs when the configured retry budget is exhausted.
- Added optional priority-aware processing lanes for already-fetched bounded
  batches, including validated lane configuration, weighted starvation
  controls, fail-closed handling for unsafe priority metadata, aggregate
  priority-lane metrics, commit-then-ACK tests, and dedicated documentation
  that explains ordering limitations.
- Added non-JSON boundary regression coverage for NATS authentication
  ambiguity, NATS URL scheme validation, TLS seed URL handling, direct
  `RetryPolicy` construction, Oracle `payload_field` idempotency, and
  negative JetStream metadata normalization.
- Added GitHub issue planning synchronization for managed bugs and backlog
  items, including required live GitHub Issue `Priority` field updates and
  native GitHub issue dependency relationships for declared `blocked_by` and
  `blocks` links.
- Added CycloneDX SBOM generation through `scripts/sbom.sh`, producing JSON and
  XML release-evidence artifacts under `dist/sbom/`.
- Added CI and release workflow steps that generate SBOM files after package
  build, upload them as workflow artifacts, and attach them to GitHub Releases
  without uploading them to PyPI.
- Added SBOM documentation covering local generation, automated release
  integration, security notes, limitations, and how operators can use SBOMs in
  vulnerability and compliance workflows.
- Added Oracle duplicate/conflict metrics for idempotent Oracle operations:
  `oracle_conflicts_total`, `oracle_duplicates_total`, and
  `oracle_duplicate_ignored_total`.
- Added tests and documentation showing how Oracle duplicate/conflict counters
  appear through the `nats-sink-metrics` CLI in table, shell, and Python
  snapshot-reading workflows.
- Added rich metrics documentation covering configuration, snapshot shape,
  shell scripting, Prometheus textfile output, Python hooks, exit codes,
  security guidance, and the metric reference.
- Added an observability core with disabled-by-default sharing policies,
  subject discovery from runtime config, allow/deny metric controls, and a
  future connector extension point separate from core delivery and sinks.
- Added the `nats-sink-observe` CLI for generating Prometheus observability
  policies, validating policies, listing available metric names, listing
  subject hints, and rendering policy-filtered Prometheus textfile output.
- Added a Prometheus textfile connector for node_exporter that reads only local
  metrics snapshots, exports no metrics unless explicitly enabled by policy,
  and avoids payloads, secrets, subjects, labels, classification values, table
  names, file paths, and high-cardinality labels by default.
- Added an optional native Prometheus HTTP scrape endpoint as a separate
  disabled-by-default observability connector that reads local metrics
  snapshots, applies the same allow-list policy as the textfile connector,
  enforces response-size and stale-snapshot controls, and avoids coupling
  endpoint failures to JetStream ACK behavior.
- Added Debian and Oracle Linux systemd assets for running the Prometheus
  textfile export as a separate oneshot service and timer from the main
  `nats-sink` worker.
- Added a disabled native Prometheus HTTP systemd service example and unified
  installer support so operators can run the scrape endpoint as a separate
  Linux service after explicit policy review.
- Added Kubernetes deployment examples with JSON ConfigMaps, Secret references,
  mounted trust material, worker and observability separation, resource limits,
  security contexts, NetworkPolicy guidance, graceful shutdown settings, and
  optional Prometheus HTTP sidecar manifests.
- Added `scripts/install-systemd.sh`, a unified systemd installer that detects
  Debian-family systems or Oracle Linux from `/etc/os-release` and applies the
  correct package-manager and service-user setup.
- Added documented Debian and Oracle Linux one-command install examples that
  download `scripts/install-systemd.sh` from GitHub and run it with `sudo`,
  plus safer review-first guidance for sensitive production environments.
- Added public observability and Prometheus documentation with diagrams,
  policy examples, CLI examples, Linux service guidance, node_exporter
  integration notes, security guidance, and future connector candidates.
- Added a public backlog-management guide that defines GitHub Issues as the
  live backlog, `CHANGELOG.md` as shipped history, and detailed close-out
  expectations for feature requests.
- Added local JSON backlog staging under `backlog/items/`, a
  `scripts/sync-backlog-issues.py` GitHub CLI sync tool, and a `Backlog Sync`
  GitHub Actions workflow for idempotently creating or updating GitHub Issues
  from local backlog definitions.
- Added generated `requirements*.txt` dependency manifests derived from
  `pyproject.toml` so GitHub Dependency Graph and Dependabot have stable
  pip-compatible manifests for runtime and optional dependency groups.
- Added `scripts/update-dependency-manifests.py` plus CI, pre-commit, and
  local check integration to ensure generated dependency manifests stay in
  sync with package metadata.
- Added dependency-management documentation covering GitHub Dependency Graph
  enablement, generated manifest maintenance, Dependabot, dependency review,
  and supply-chain security boundaries.
- Added detailed local backlog JSON items for all currently unrealized Phase 2
  and Phase 3 roadmap work so the roadmap can be synchronized into GitHub
  Issues as actionable enhancement requests.
- Added `target_release` support to backlog JSON sync so issues receive
  `release-unscheduled` or concrete release labels before implementation work
  starts.
- Changed managed backlog and bug sync so priority is maintained through the
  official GitHub Issue `Priority` field instead of issue labels. The sync
  tools now remove legacy `priority-p*` labels from managed issues during
  update and support an explicit Issue field ID for automation tokens that can
  edit issues but should not enumerate organization Issue fields.
- Added managed issue workflow support for a `completed` label on bug reports
  and feature requests after local implementation evidence has been posted,
  keeping fixed or implemented issues open but clearly marked while they wait
  for release-gated closure.
- Added a managed bug-report workflow with local sanitized JSON staging under
  `bugs/reports/`, a `scripts/sync-bug-reports.py` GitHub CLI sync tool,
  severity and priority labels, default assignment to `louwersj`, and a
  dedicated `Bug Report Sync` GitHub Actions workflow.
- Added `scripts/comment-bug-issue.py` for test-driven bug lifecycle comments
  requiring failing-test evidence before fixes and regression, verification,
  and close-out evidence after fixes, including an optional sanitized
  `--test-file` attachment for small focused regression tests.
- Added release workflow integration through `scripts/close-released-bug-issues.py`
  so managed bug reports close only after the associated GitHub Release exists,
  acceptance criteria are checked, and sanitized fix evidence is present.
- Expanded the public bug report issue form, backlog-management guide,
  `ROBOTS.md`, and `AGENTS.md` with the required bug-report, TDD, evidence,
  release-label, and release-gated close-out workflow.
- Added public-safety validation to backlog sync and backlog comment tooling
  so local enhancement requests and implementation notes reject common leak
  patterns before they reach GitHub Issues.
- Added `scripts/comment-backlog-issue.py` for sanitized progress comments,
  release-label updates, and release-gated close-out comments that verify the
  GitHub Release before closing an enhancement request.
- Added `scripts/generate-checksums.py` and release workflow integration to
  attach a `SHA256SUMS` manifest for wheel, source distribution, and SBOM
  artifacts to GitHub Releases.
- Added [Hash-Verified Installs](docs/hash-verified-installs.md) guidance for
  pinned, hash-checked `pip --require-hashes` deployments in high-trust
  environments.
- Added release workflow automation that closes managed backlog issues labeled
  for a release only after the associated GitHub Release exists.
- Added stricter backlog issue lifecycle enforcement: start comments require
  planned work, test plan, and documentation/release-note sections; completion
  comments require completed work, acceptance criteria, test-plan evidence, and
  close-out evidence sections.
- Added backlog helper support for assigning issues, marking Acceptance
  Criteria checklist items complete, removing stale `release-unscheduled`
  labels when a concrete release label is applied, and preventing release
  automation from closing issues that lack checked acceptance criteria or
  close-out/test evidence.
- Added a detailed backlog item for a future native Oracle Cloud Infrastructure
  Object Storage sink, including functional requirements, non-functional
  requirements, security expectations, test planning, documentation scope, and
  release success criteria.
- Expanded the GitHub feature request issue form and pull request template so
  backlog items capture operational context, delivery semantics, security
  considerations, acceptance criteria, test plans, documentation plans, and
  close-out evidence.
- Added unit coverage for observability policy generation, policy validation,
  Prometheus allow/deny filtering, observation suppression, textfile writing,
  and the new observability CLI.
- Added explicit testing and Oracle documentation for retained e2e table schema
  drift, fresh current-schema test tables, and backend timing metrics as
  functional observations rather than production benchmarks.
- Added a deterministic synthetic mission scenario harness under
  `nats_sinks.testing`, plus `scripts/run-synthetic-harness.py`, for generating
  sanitized fake `NatsEnvelope` scenarios covering valid JSON, malformed
  JSON-like text, duplicates, stale timestamps, encrypted-payload markers,
  NATO-style classification values, priority values, labels, and empty
  payloads without requiring live NATS or Oracle services.
- Added file-sink synthetic harness coverage and documentation so maintainers
  can run local smoke scenarios with uncompressed or gzip-compressed file
  output while keeping generated files under ignored local paths by default.
- Added use-case documentation pages for defence and mission-support patterns,
  including synthetic mission testing guidance that keeps domain-specific
  examples in documentation while preserving a generic sink framework.
- Added an F2T2EA event phase tagging blueprint that documents metadata-only
  lifecycle tagging, allowed example phase values, explicit non-goals, Oracle
  mission metadata JSON column examples, file sink record examples, and
  sanitized tracked JSON examples for validation.
- Added broader defence and mission-support blueprint pages for sensor event
  custody, classification and labels, chain of custody, cross-domain handoff
  preparation, edge operation, and audit-oriented persistence. The pages
  explain current generic nats-sinks features without making the product
  defence-only or implying targeting, fire-control, weapons-release, or
  autonomous decision behavior.
- Added a generic mission metadata profile that can resolve one validated JSON
  context object from a NATS header, global defaults, or subject-aware defaults
  before a message reaches any sink.
- Added `NatsEnvelope.mission_metadata` and
  `mission_metadata_for_json_storage()` so future sinks can preserve the same
  validated context without depending on Oracle- or file-specific behavior.
- Added Oracle `MISSION_METADATA_JSON` mapping and recommended table DDL so
  richer mission, operation, platform, source-system, track, confidence,
  releasability, or lifecycle metadata can be stored without adding fixed
  columns for every profile field.
- Added file sink output support for top-level `mission_metadata` and
  `metadata.mission_metadata`.
- Added unit tests for mission metadata parsing, duplicate-key rejection,
  subject-aware defaults, profile allow-lists, size limits, secret-like key
  rejection, file sink output, Oracle row mapping, and DLQ-before-ACK handling
  for invalid metadata.
- Added deterministic bounded property-style generator tests for subject
  matching, payload normalization, message metadata normalization, mission
  metadata validation, and file path sanitization without adding a new
  dependency.
- Added Oracle benchmark tooling with `scripts/run-oracle-benchmark.sh` and
  `scripts/run-oracle-benchmark.py`, reporting publish, fetch, map, Oracle
  execute, Oracle commit, ACK, retry-delay, and shutdown timing as sanitized
  environment-specific observations.
- Added benchmark report helpers under `nats_sinks.testing` so report
  redaction, phase aggregation, and command validation are covered by unit
  tests without live NATS or Oracle services.
- Added sanitized synthetic load-test profiles with
  `scripts/run-load-profile.py` and `scripts/run-load-profile.sh`, covering
  normal, retry, DLQ, shutdown, optional encryption-workload, and
  metrics-snapshot behavior without live services.

### Changed

- Standardized SPDX source headers across Python and shell files with
  `SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>` and
  `SPDX-License-Identifier: Apache-2.0`.
- Hardened JSON configuration loading so config files are size-bounded,
  duplicate-key checked, UTF-8 checked, and required to use a JSON object at
  the root.
- Hardened CLI logging setup so unknown log levels fail closed and log messages
  escape control characters before reaching terminals or log collectors.
- Wired release-version consistency checking into the local check script, CI,
  and pre-commit.
- Kept legacy metrics aliases such as `batch_write_seconds` and
  `messages_received_total` while documenting the clearer preferred names such
  as `sink_batch_write_seconds` and `messages_prepared_total`.
- Extended `metrics` configuration with `snapshot_file` so `nats-sink run` can
  write a local JSON snapshot when metrics are enabled.
- Updated Debian and Oracle Linux install scripts to install disabled
  observability policy examples and Prometheus textfile systemd units without
  enabling external sharing by default.
- Changed the older distribution-specific systemd install scripts into
  compatibility wrappers that delegate to the unified
  `scripts/install-systemd.sh` installer.
- Changed the unified systemd installer so it can run from a git checkout or
  as a standalone downloaded script by fetching required example config and
  systemd unit files from GitHub using `NATS_SINKS_INSTALL_REF`.
- Changed standalone systemd installs so tagged installer runs default to the
  matching PyPI package version, with `NATS_SINKS_PACKAGE_SPEC` available for
  optional extras such as `nats-sinks[oracle]`.
- Expanded public mission-oriented wording to explicitly describe
  sensor-driven warfighting support contexts such as sensor-fusion,
  command-and-control, sensor-to-shooter, kill-chain, and kill-mesh data
  flows, while clearly stating that `nats-sinks` is not targeting,
  fire-control, weapons-release, or lethal decision-making software.
- Extended `delivery` configuration with `retry_backoff_max_ms`,
  `retry_backoff_mode`, `retry_backoff_multiplier`, and `retry_jitter`.
- Added `cyclonedx-bom` to development dependencies because SBOM generation is
  a build and release evidence task, not a runtime dependency.

### Fixed

- Fixed NATS configuration validation so primary and seed URLs fail closed
  unless they use supported NATS client schemes: `nats`, `tls`, `ws`, or
  `wss`.
- Fixed NATS authentication validation so token, username/password,
  credentials-file, and NKEY seed-file modes are mutually exclusive, and
  username/password mode requires both a username and exactly one password
  source.
- Fixed CLI NATS option construction so a TLS context is created when any
  configured seed URL uses `tls://`, not only when the fallback primary URL
  uses TLS.
- Fixed direct `RetryPolicy` construction so invalid negative values, unknown
  runtime modes, non-finite multipliers, and impossible caps are rejected
  consistently with JSON configuration validation.
- Fixed exponential retry backoff so extreme delivery attempts return the
  configured cap instead of raising before jitter or delayed NAK handling.
- Fixed Oracle `payload_field` idempotency validation so empty path segments
  and control characters are rejected during configuration validation.
- Fixed Oracle `payload_field` idempotency extraction so objects and arrays are
  rejected as ambiguous keys instead of being converted to language-specific
  string representations.
- Fixed NATS consumer normalization so negative JetStream sequence, delivery,
  and pending metadata values are treated as absent rather than persisted in
  envelopes.
- Fixed runtime package version drift by aligning `nats_sinks.__version__` with
  the `0.3.0` package metadata.
- Fixed managed bug-report test attachments so shell scripts, Python files,
  JSON, Markdown, TOML, YAML, and plain-text files render with matching
  Markdown code fences instead of always using a Python fence.
- Fixed file sink path sanitization so a hostile or unusual value whose string
  conversion fails produces a bounded fallback filename component instead of an
  unexpected sanitizer exception.
- Fixed synthetic load-profile phase-rate reporting so shutdown, DLQ,
  backend-write, ACK, retry, and encryption phase throughput uses the
  phase-specific completed-work counter instead of the total generated message
  count.
- Fixed Oracle benchmark report interpretation so retry-delay and shutdown
  phases are timing-only observations and no longer report misleading
  messages-per-second values.
- Fixed payload JSON parsing so Python-only constants such as `NaN` and
  `Infinity` are not treated as valid JSON. `json_only` mode now raises a
  serialization error, while `json_or_envelope` preserves the original text in
  the payload envelope.
- Fixed payload JSON parsing so duplicate object keys are treated as
  ambiguous. `json_only` mode now fails closed, while `json_or_envelope`
  preserves the original body as text instead of silently keeping only the last
  duplicate value.
- Fixed metrics snapshots so non-finite metric values are rejected before
  local JSON snapshot writing or loading can produce non-standard JSON.
- Fixed the metrics CLI description path so strict type checking and Ruff
  validation pass under the release CI matrix.
- Fixed the release workflow artifact layout so PyPI publishing receives only
  wheel and source distribution files. `SHA256SUMS` remains release evidence
  and is attached to the GitHub Release instead of being uploaded to PyPI.
- Fixed the optional NATS server monitoring connector so endpoint responses and
  stored snapshots reject non-standard JSON constants before observability
  output is generated.
- Fixed the optional NATS server monitoring connector so duplicate endpoint
  response keys are rejected before allow-listed fields are extracted.
- Fixed strict JSON handling across configuration loading, backlog and
  bug-report sync manifests, mission metadata headers, encryption envelopes,
  observability policy writing, Oracle benchmark reports, and synthetic
  load-profile reports. These paths now reject duplicate keys, non-standard
  constants, or non-finite timing values before public evidence or sink-facing
  data can be generated.

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
