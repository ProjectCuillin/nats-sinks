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
- Keep formatting, linting, typing, tests, packaging, and documentation checks
  green before considering work complete.

## Security And Coding Standards

- Never log secrets, credentials, tokens, private keys, full connection strings,
  wallet material, or sensitive payloads by default.
- Validate external input, especially SQL identifiers, NATS subject routing
  patterns, file paths, and configuration fields.
- Use bind variables for values and strict allow-list validation for SQL
  identifiers.
- Do not add dependencies without a clear reason and corresponding docs.
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
- Update `CHANGELOG.md` for user-visible changes.
- Keep documentation and `CHANGELOG.md` ready for the next release at all
  times. Add user-visible work to the `Unreleased` section immediately, and do
  not rely on local notes or chat history as release documentation.
- Keep README documentation links PyPI-safe with fully qualified public URLs,
  and keep `docs/` page-to-page links relative so Read the Docs preserves
  version-local navigation.
