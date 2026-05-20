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
- Live integration tests must fail fast on stale retained database schemas and
  should use explicit fresh test table names when validating new storage
  behavior.
- Keep formatting, linting, typing, tests, packaging, and documentation checks
  green before considering work complete.

## Security And Coding Standards

- Never log secrets, credentials, tokens, private keys, full connection strings,
  wallet material, or sensitive payloads by default.
- Never log payload encryption keys, generated key material, plaintext payloads,
  or decrypted payloads by default.
- Validate external input, especially SQL identifiers, NATS subject routing
  patterns, file paths, and configuration fields.
- Use bind variables for values and strict allow-list validation for SQL
  identifiers.
- Do not add dependencies without a clear reason and corresponding docs.
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
- Update `CHANGELOG.md` for user-visible changes.
- Keep documentation and `CHANGELOG.md` ready for the next release at all
  times. Add user-visible work to the `Unreleased` section immediately, and do
  not rely on local notes or chat history as release documentation.
- Use mission, defence, public-sector, and operational wording only where it
  helps external readers understand real deployment concerns. Keep it subtle
  and accurate; never imply official endorsement, accreditation, tactical
  suitability, exactly-once delivery, or guarantees beyond the documented
  at-least-once commit-then-ACK model.
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
- The default Oracle schema includes nullable `PRIORITY` and `CLASSIFICATION`
  columns. Any future Oracle schema change must update DDL helpers, column
  mapping docs, least-privilege setup examples, retained-schema checks, and live
  e2e tests in the same change.
- Live e2e timing output is an observation from one environment, not a
  benchmark. Keep reports careful and avoid production throughput claims unless
  a benchmark plan, environment, and repeatability criteria are documented.
