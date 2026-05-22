# Agent Guide

AI agents working in this repository must follow both this file and
`ROBOTS.md`. If the two files appear to conflict, apply the stricter safety
rule and update both documents in the same change.

## Non-Negotiable Delivery Invariant

> Commit first. ACK last. Design for redelivery.

Never ACK a JetStream message before durable sink success. If durable work has
not committed, the correct failure mode is redelivery, NAK, timeout, or DLQ
handling according to the framework policy. A duplicate message is recoverable;
silent loss after an early ACK is not.

## Reliability Expectations

- Treat malformed payloads, invalid configuration, unexpected client objects,
  network timeouts, authentication failures, database errors, DLQ publish
  failures, and ACK failures as normal production events.
- Do not let the long-running processing loop terminate from an avoidable
  application exception. Classify framework errors, log safely, and prefer
  redelivery when the durable state boundary is uncertain.
- Keep core-owned transformations in the core. Payload encryption,
  priority/classification metadata resolution, message normalization, DLQ
  construction, and ACK decisions belong before or around
  `sink.write_batch(...)`, not inside destination-specific sink shortcuts.
- Subject-specific encryption rules are core behavior. Use the shared NATS
  subject matcher, preserve ordered first-match-wins semantics, and test
  matching subjects, unmatched subjects, disabled-rule exemptions, and global
  fallback behavior.
- Never add a sink that can silently lose messages.
- Never weaken idempotency behavior without updating tests, documentation, and
  `CHANGELOG.md`.
- Do not catch broad exceptions and continue silently. Boundary-level catches
  must log safe context, preserve exception chaining where re-raised, and have
  tests proving ACK behavior.

## Testing Requirements

- Add deterministic unit tests for unhappy paths, not only the happy path.
- Use fuzz-style deterministic tests for validators, parsers, and normalizers
  when external input is accepted.
- Use bounded property-style generator tests for security-sensitive validators
  and normalizers when a small deterministic corpus can cover many edge cases.
  Keep the corpus fake, sanitized, and reproducible. Do not add Hypothesis or a
  similar dependency unless the benefit is clear, development-only, documented,
  and reflected in generated dependency manifests.
- Unit tests must never make network calls and must remain deterministic.
- Integration tests must stay behind explicit markers and environment gates.
- Preserve only the latest sanitized test report at `docs/test-report.md`.
  Never commit raw logs, live server addresses, usernames, passwords, tokens,
  certificate contents, wallet material, full connection strings, or sensitive
  payloads in test reports.
- When delivery semantics change, add or update tests proving ACK-after-commit,
  no ACK on sink failure, DLQ-before-ACK, and no ACK when DLQ publish fails.
- When payload encryption changes, test encrypted and unencrypted paths for
  each production sink where practical, and prove stored encrypted envelopes
  decrypt back to the original JSON, text, empty, and binary payload bytes.
- When message metadata changes, test priority, classification, and labels
  with all fields present, only one field present, no fields present, defaults
  applied, subject-specific defaults applied, first-match-wins rule behavior,
  unmatched fallback, explicit null or empty subject defaults, and explicitly
  empty headers becoming null or no labels. Cover every production sink where
  practical.
- When retry behavior changes, test fixed, linear, exponential, capped,
  jittered, and no-jitter paths. Also test retry budget exhaustion and prove
  the runner still does not ACK messages whose durable sink work failed.
- Live integration tests must fail fast on stale retained database schemas and
  should use explicit fresh test table names when validating new storage
  behavior.
- Keep formatting, linting, typing, tests, packaging, and documentation checks
  green before considering work complete.
- Never make ordinary code, documentation, release-note, or automation changes
  directly on `main`. Use the hierarchical branch model: `release-vX.Y.Z`
  from `main`, issue or feature branches from the release branch, and bug
  branches from the active issue or feature branch when defects are found
  during development.
- Merge bug branches back into their issue or feature branch after the failing
  test, fix, and sanitized evidence are complete. Merge issue or feature
  branches back into the release branch after implementation evidence,
  documentation, changelog updates, and local checks are complete. Merge the
  release branch into `main` only when the maintainer explicitly decides to
  release.
- Keep ordinary branch pushes quiet. Do not trigger GitHub Actions after every
  small branch commit. Use `scripts/open-release-pr.sh --base <target-branch>`
  to open or refresh a draft pull request against the correct hierarchy
  target, then run `scripts/run-release-validation.sh` only when the branch is
  ready for merge or release validation.
- For issue, feature, and bug pull requests raised by this workflow,
  `scripts/open-release-pr.sh --ready` auto-approves ready non-main PRs by
  default. Use `--no-auto-approve-non-main` only when manual inspection is
  needed before approval. The approval helper must refuse PRs targeting
  `main`, should verify the expected author when possible, and does not
  replace release validation, maintainer review, issue evidence, docs, or
  changelog work. Release PRs into `main` are always manual.
- The `Branch Pull Request` workflow is manual and token-gated. Do not
  re-enable push-triggered pull request creation unless the maintainer
  explicitly changes the release policy. Do not create release tags from
  unmerged work branches; the release workflow rejects tags that are not
  already contained in `main`.
- Treat `main` branch protection as part of the safety model. CODEOWNER review,
  required CI, stale-review dismissal, resolved conversations, and no force
  pushes are expected for every release-boundary merge.
- Treat GitHub Issues as the live backlog and `CHANGELOG.md` as the shipped
  release history. Before implementing user-visible work, look for a matching
  GitHub issue or prepare a detailed feature request unless the change is a
  small typo or mechanical maintenance item.
- If a new backlog item is discovered locally and live GitHub access is not
  available, create a JSON backlog item under `backlog/items/`, validate it
  with `python scripts/sync-backlog-issues.py --dry-run`, and explain that it
  still needs to be synced to GitHub.
- Backlog JSON must include `target_release`. Use `unscheduled` until the work
  is assigned to a concrete release tag, then use a release tag such as
  `v0.4.0` so the live issue receives a matching release label.
- Backlog and bug JSON must include one of the supported priority values. The
  sync tooling writes the official GitHub Issue `Priority` field. Agents
  should not invent alternate priority labels, recreate legacy `priority-p*`
  labels on issues, or bypass Issue-field sync for normal backlog and bug
  work.
- If an issue is blocked by, blocks, or is related to another managed issue,
  declare the relationship in local JSON with safe references only:
  `#123`, `backlog:item-id`, or `bug:item-id`. Let the sync tooling populate
  GitHub-native issue dependencies where supported. Never write private URLs,
  IP addresses, system names, payloads, or secret-bearing context into issue
  relationship fields or comments.
- Before syncing Issue fields locally, confirm that `gh` can update issues with
  `gh auth status --hostname github.com`; if not, ask the maintainer to run
  `gh auth refresh -s repo -s read:org` interactively. In automation, prefer
  `NATS_SINKS_GITHUB_ISSUE_PRIORITY_FIELD_ID` when a token can edit issues but
  should not enumerate organization Issue fields.
- Never include secrets, credential values, certificate material, private
  operational details, live network locators, IP addresses, sensitive
  subjects, or payload examples in backlog JSON, generated issue bodies,
  progress comments, close-out comments, or GitHub feature requests.
- Before posting progress or close-out notes, validate the text with
  `python scripts/comment-backlog-issue.py --dry-run`. Progress comments should
  explain the intended implementation approach, expected tests, documentation
  changes, and planned release tag.
- Before starting issue work, assign the issue to the maintainer doing the
  work, apply a concrete release label, and post a sanitized `started`
  lifecycle note with `Planned Work`, `Test Plan`, and
  `Documentation And Release Notes` sections.
- Before editing code for a managed issue, create or switch to the issue branch
  from the active release branch. If a bug is discovered inside that feature
  branch, create a separate bug report, branch from the feature branch, add the
  failing test first, and merge the bug branch back into the feature branch
  after verification.
- When implementation is complete locally, post a sanitized `completed` or
  `closeout` lifecycle note with `Completed Work`, `Acceptance Criteria`,
  `Test Plan Evidence`, and `Close-Out Evidence` sections. Use
  `--complete-acceptance` only after the documented test plan has actually
  been executed and the evidence has been summarized without secrets, private
  locators, IP addresses, credentials, certificate material, or sensitive
  payloads. The managed helper applies the `completed` label for `completed`,
  `closeout`, and `released` statuses. This label means done in development
  and waiting for release-gated closure; it does not close the issue.
- Link implemented issues from the pull request with `Related #...` unless the
  issue should close immediately for a non-release maintenance reason. Feature
  requests close only after the associated release has actually been published.
  Release automation verifies the GitHub Release, checked acceptance criteria,
  and close-out/test evidence before closing managed issues. If GitHub
  authentication is unavailable, prepare the issue body or lifecycle comment
  and say clearly that the live issue was not changed.
- Treat bugs discovered during tests, review, or release preparation as
  managed GitHub bug reports. Create or sync sanitized local bug JSON under
  `bugs/reports/*.json` with `python scripts/sync-bug-reports.py --dry-run`
  before fixing anything beyond a tiny non-user-visible typo.
- Bug work must be test driven. Add the smallest focused failing regression
  test first, include it in the normal test suite, and post a sanitized
  `failing-test` lifecycle comment with `scripts/comment-bug-issue.py --dry-run`
  before implementing the fix.
- Managed bug reports must be assigned to `louwersj`, labeled with `bug`,
  severity, priority, and release labels, and kept open until the release that
  contains the fix has actually been published. Release automation closes bug
  reports only after checked acceptance criteria and sanitized regression,
  verification, and close-out evidence are present. The managed bug comment
  helper applies the `completed` label for completed bug fixes so maintainers
  can filter fixed-but-not-yet-released defects.

## Security And Coding Standards

- Never log secrets, credentials, tokens, private keys, full connection strings,
  wallet material, or sensitive payloads by default.
- Apply the full production hardening checklist in `ROBOTS.md` before
  completing a change. Treat every external input as hostile; validate,
  normalize, bound, and type it at the boundary; and fail closed when the safe
  behavior is ambiguous.
- Use least privilege in code, examples, CI, database guidance, service files,
  containers, and cloud identities. Do not grant destructive or administrative
  rights when insert, read, or write-scoped permissions are enough.
- Threat-model important changes before implementation. Identify assets, trust
  boundaries, abuse cases, attacker capabilities, and worst-case failure modes,
  then add tests or documentation for the security invariants that must hold.
- Keep security-sensitive logic centralized and easy to review. Authentication
  option construction, TLS context creation, configuration parsing, SQL
  identifier validation, payload encryption, redaction, log sanitization, ACK
  decisions, and DLQ shaping should not be duplicated ad hoc across sinks.
- Use allow-list validation for enum values, lengths, ranges, formats, SQL
  identifiers, sink types, route names, NATS subject patterns, file extensions,
  URL schemes, and operational modes.
- Reject malformed or ambiguous input early. Do not repair, guess, silently
  coerce, or partially accept security-relevant input.
- Keep data separate from code in SQL, shell commands, templates, regexes,
  serializers, logs, and browser contexts. Do not concatenate untrusted data
  into executable or interpretable strings.
- Treat log output as an injection surface. Sanitize newlines, terminal escape
  sequences, control characters, and attacker-controlled formatting while still
  preserving enough safe context for operators.
- Avoid dynamic execution, unsafe dynamic imports, `eval`, `exec`, pickle,
  unsafe YAML loaders, shell interpolation, mutable default arguments, broad
  monkey-patching, and hidden global state.
- Prefer bounded, parser-backed, typed input handling. Enforce maximum sizes for
  config files, payloads, arrays, queues, batches, retries, and downstream
  native-library inputs.
- Never log payload encryption keys, generated key material, plaintext payloads,
  or decrypted payloads by default.
- For payload encryption key rotation work, preserve the encrypted envelope
  schema, keep `key_id` values non-secret and bland, and test old/new key
  decryption through `PayloadKeyRegistry`.
- Keep provider-specific secret-manager SDKs out of the core package unless a
  future optional extra and connector are explicitly designed, documented, and
  tested.
- Validate external input, especially SQL identifiers, NATS subject routing
  patterns, file paths, and configuration fields.
- Use bind variables for values and strict allow-list validation for SQL
  identifiers.
- Do not add dependencies without a clear reason and corresponding docs.
- When dependencies change, update `pyproject.toml`, regenerate
  `requirements*.txt` with `python scripts/update-dependency-manifests.py`,
  run `python scripts/update-dependency-manifests.py --check`, and document the
  reason. Do not hand-edit generated dependency manifests.
- Treat SBOM generation as a build and release-evidence concern. If package
  build behavior, dependency metadata, release automation, or supply-chain
  guidance changes, keep `scripts/sbom.sh`, CI, release workflow artifacts,
  `docs/sbom.md`, `docs/release.md`, `docs/publishing.md`, and
  `CHANGELOG.md` in sync.
- Do not put secrets, payloads, live service details, Oracle wallet material,
  certificates, private keys, local `.local/` configuration, or test
  credentials into SBOM artifacts, SBOM examples, release reports, or generated
  documentation.
- Keep runtime version metadata aligned. A change to `pyproject.toml` version
  requires matching updates to `src/nats_sinks/__init__.py`, README release
  text, `docs/index.md`, and `CHANGELOG.md`, and the version consistency check
  must pass.
- Treat README and documentation import examples as public API contracts.
  Before moving or removing a documented import path, update tests,
  documentation, and `CHANGELOG.md`, and make the compatibility decision
  explicit.
- Treat generated `site/` HTML as output. Edit README, `docs/`, `mkdocs.yml`,
  and source files rather than generated pages, then rebuild the site.
- Keep `docs/security-rule-review.md` current when security posture changes.
  New sinks, parsers, authentication modes, filesystem behavior, crypto
  behavior, native dependencies, HTTP/web/upload/plugin features, or release
  workflow changes may require reopening controls previously marked
  `Not applicable`.
- Treat each new destination as a sink connector with documented durable
  success, idempotency, security, and certification requirements. Oracle
  Database and FileSink are first-party built-ins; future Oracle-family sinks
  should also be first-party connectors unless governance explicitly changes
  that posture.
- Keep external connector discovery disabled by default and allow-list based.
  Never let JSON configuration choose arbitrary module paths, class paths, or
  dynamic imports. External connectors must expose `SinkConnector` metadata,
  match the allow-listed entry-point name, and pass certification tests before
  production recommendation.
- Palantir Foundry, Palantir Gotham, and other third-party platform connectors
  require local fake clients or contract harnesses before live certification is
  attempted. Do not imply production certification from public documentation
  alone.
- Prefer environment-backed encryption key fields such as
  `encryption.key_b64_env`; never commit direct encryption key material.
- Document and test `encryption.rules` whenever they change. Rule order is a
  security-relevant configuration choice because the first matching subject
  rule wins and disabled rules intentionally bypass encryption for matching
  subjects.
- Generated test key material must be deleted by default and preserved only
  through an explicit local debug flag. Never include generated keys in
  Markdown reports, screenshots, CI logs, examples, or committed config.
- Payload encryption protects payload bytes only. Do not imply that subjects,
  headers, message IDs, stream sequences, priority, classification, labels,
  table names, file paths, timestamps, or other metadata are encrypted.
- Metrics are operational signals and must not affect delivery semantics. Add
  or rename metrics through `src/nats_sinks/core/metrics.py`, document them,
  keep exporter labels low-cardinality, avoid sensitive metadata as labels, and
  preserve compatibility aliases where practical.
- Observability connectors are separate from the sink runner. The runner writes
  local metrics snapshots; policy-controlled commands such as
  `nats-sink-observe` decide what can be shared with Prometheus or future
  platforms. Default sharing must remain off.
- Prometheus support must remain policy driven and least-privileged. Keep the
  textfile connector separate from the main sink service, avoid network access
  in the connector, and export only allow-listed metrics.
- Do not export sensitive or high-cardinality labels by default. Subjects,
  message IDs, stream sequence values, table names, file paths, priority,
  classification, labels, payload fields, usernames, and host-specific secrets
  require explicit design review and documentation before any connector can
  publish them.
- Subject-aware observability must remain disabled by default until a reviewed
  policy model, bounded subject-family aggregation, and certification tests are
  in place. Prefer stable operator-approved family labels over raw subjects,
  enforce cardinality caps, and do not treat hashed subjects as automatically
  non-sensitive.
- Keep NATS server monitoring endpoint support outside the delivery runner.
  `JetStreamSinkRunner` must not poll `/jsz`, `/healthz`, or similar endpoints.
  Any `nats-sink-observe` connector must be disabled by default, validate the
  monitoring URL and endpoint paths, bound timeouts and response size, extract
  only allow-listed scalar fields, avoid storing the base URL in snapshots, and
  never influence ACK, retry, DLQ, or sink-write decisions.
- Use the synthetic mission scenario harness for repeatable local edge-case
  evidence when real NATS or Oracle services are not needed. Keep generated
  data fake, sanitized, deterministic, and free of live locators, credentials,
  certificates, keys, private payloads, and operational details. Live service
  adapters must stay separate and explicitly gated.
- Oracle duplicate/conflict counters are safe observability signals only. Do
  not include table names, subjects, constraint names, message IDs, payloads,
  classification values, labels, or secrets in those metrics, and never use
  them to decide ACK behavior.
- The `nats-sink-metrics` CLI reads local JSON snapshots only. Do not make it
  connect to NATS, Oracle, file sinks, cloud services, or future destinations.
  Keep its output formats deterministic, pipe-friendly, redacted, and covered
  by unit tests.
- Metrics snapshots must remain bounded, schema-versioned, duplicate-key
  checked, UTF-8 checked, and free of payloads, secrets, credentials,
  certificate material, and private key material.
- Treat classification labels as potentially sensitive metadata. Do not expose
  them in broad debug dumps, public reports, or high-cardinality metrics labels
  without explicit documentation.
- Use stable metadata-based idempotency such as stream sequence or message ID
  when encryption is enabled. Ciphertext is intentionally non-deterministic
  because each encryption uses fresh nonce material.
- Do not use priority, classification, or labels as idempotency keys unless a
  future design explicitly proves they are unique and safe. They are
  operational metadata, not duplicate-detection keys.
- Prefer small, reviewable changes with focused tests.
- Maintain public API stability unless a breaking change is intentional,
  documented, and represented in the changelog.

## Documentation And Comments

- Public modules, public classes, public functions, and non-obvious reliability
  logic must be documented.
- Source comments should explain why safety-sensitive code exists, not merely
  restate what each line does.
- Update Markdown documentation whenever public behavior, configuration,
  operational guidance, security posture, or release procedure changes.
- When changing priority/classification/labels behavior, update the core
  configuration reference, sink-specific storage docs, examples, tests,
  `docs/test-report.md`, and `CHANGELOG.md` together.
- When changing mission metadata behavior, update the generic
  `docs/mission-metadata.md` page, sink-specific storage docs, use-case
  blueprints, examples, tests, `docs/test-report.md`, and `CHANGELOG.md`
  together.
- Treat mission metadata as hostile JSON until it has been parsed, duplicate-key
  checked, bounded, profile-checked when configured, and screened for
  secret-looking field names. Invalid mission metadata must follow permanent
  validation failure handling and DLQ-before-ACK semantics.
- Update `CHANGELOG.md` for user-visible changes.
- Keep documentation and `CHANGELOG.md` ready for the next release at all
  times. Add user-visible work to the `Unreleased` section immediately, and do
  not rely on local notes or chat history as release documentation.
- Use mission, defence, public-sector, and operational wording only where it
  helps external readers understand real deployment concerns. Keep it subtle
  and accurate; never imply official endorsement, accreditation, tactical
  suitability, exactly-once delivery, or guarantees beyond the documented
  at-least-once commit-then-ACK model.
- Treat F2T2EA phase tagging, kill-chain, kill-mesh, and similar lifecycle
  concepts as metadata and documentation patterns unless a separate generic
  feature has been designed and tested. Never imply that nats-sinks performs
  targeting, fire-control, weapons release, rules-of-engagement evaluation, or
  autonomous decision-making.
- Keep README documentation links PyPI-safe with fully qualified public URLs,
  and keep `docs/` page-to-page links relative so Read the Docs preserves
  version-local navigation.

## Destination-Specific Operational Lessons

- Oracle JSON columns can be returned by `python-oracledb` as strings, LOBs,
  dictionaries/lists, or mappings containing `Decimal` values. Test helpers and
  diagnostics must normalize those shapes before parsing or decrypting stored
  JSON payloads.
- Retained Oracle integration tables should be checked for required columns
  before writing. If an old layout is detected, fail with an actionable message
  and let the operator choose a fresh table or an explicit drop/recreate flag.
- The default Oracle schema includes nullable `PRIORITY`, `CLASSIFICATION`,
  and `LABELS` columns. Any future Oracle schema change must update DDL
  helpers, column mapping docs, least-privilege setup examples,
  retained-schema checks, and live e2e tests in the same change.
- Live e2e timing output is an observation from one environment, not a
  benchmark. Keep reports careful and avoid production throughput claims unless
  a benchmark plan, environment, and repeatability criteria are documented.
