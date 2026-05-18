# Instructions for AI Agents

This repository is safety-sensitive infrastructure code. Follow these rules:

- Never ACK a JetStream message before durable sink success.
- Never introduce a sink that can silently lose messages.
- Never log secrets, credentials, tokens, full connection strings, private keys, or sensitive payloads by default.
- Never weaken idempotency behavior without updating tests and documentation.
- Never add dependencies without a clear reason.
- Never make network calls in unit tests.
- Keep unit tests deterministic.
- Keep integration tests isolated behind markers.
- Keep only the latest sanitized test report at `docs/test-report.md`; never
  commit raw logs, server addresses, usernames, passwords, tokens, certificate
  contents, wallet material, connection strings, or sensitive payloads.
- Treat malformed payloads, invalid commands, invalid configuration, network failures, database failures, and DLQ failures as first-class production paths.
- Add deterministic unhappy-path and fuzz-style tests for validators, parsers, normalizers, and delivery decisions.
- Prefer small, reviewable changes.
- Update docs when public behavior changes.
- Update `CHANGELOG.md` for user-visible changes.
- Keep documentation and `CHANGELOG.md` prepared for the next release after
  every change. The `Unreleased` section should describe user-visible work
  before a release branch, tag, or package build is started.
- Maintain public API stability.
- Keep security, typing, linting, packaging, and tests green.
- Treat `commit-then-acknowledge` as a non-negotiable invariant.
- Document public code and add comments around safety-sensitive or non-obvious logic.

The goal of every change is to keep `nats-sinks` boring in production: clear
interfaces, predictable failures, secure defaults, well-tested behavior, and no
surprising side effects during import, startup, shutdown, or message handling.

Architecture rule:

> Core owns delivery semantics. Sinks own destination writes.

Destination sinks must not receive raw NATS messages and must never call ACK, NAK, TERM, or any other JetStream acknowledgement method.

Required processing order:

1. Receive the message.
2. Validate that the message can be processed.
3. Execute business logic.
4. Persist or commit durable state.
5. ACK only after successful completion.

Prefer safe duplication over silent loss.

## Production Python Standards

Use modern Python practices that make the package dependable for external
users, downstream maintainers, and automated operations teams:

- Keep imports side-effect free. Importing `nats_sinks` must not open network
  connections, read secrets, start background tasks, mutate global process
  state, or configure application-wide logging unexpectedly.
- Keep public APIs small, typed, documented, and stable. If a public symbol,
  config field, CLI option, or documented behavior changes, update tests,
  documentation, and `CHANGELOG.md`.
- Prefer explicit dependency injection for NATS clients, sinks, clocks, retry
  policies, and metrics hooks so tests can use mocks without network calls.
- Use `dataclasses`, `Protocol`, and Pydantic models intentionally. Data
  structures should communicate whether they are immutable runtime values,
  validated configuration, or replaceable interfaces.
- Preserve exception chaining with `raise ... from exc` when translating driver
  or library exceptions into framework-defined errors.
- Avoid broad mutable module state. If state is needed, keep it instance-local,
  protected by lifecycle methods, and safe to initialize and close more than
  once.
- Treat async code carefully. Do not call blocking network, database, file, or
  subprocess operations from the event loop unless they are explicitly isolated
  or documented as safe.
- Bound memory use. Any buffer, queue, cache, retry loop, or batch collector
  must have clear limits and predictable shutdown behavior.
- Close resources deterministically. NATS connections, Oracle pools, cursors,
  tasks, and metrics exporters must be closed in normal shutdown and failure
  paths.
- Do not add runtime dependencies unless they materially improve reliability,
  security, maintainability, or user experience. Prefer the standard library
  when it is sufficient.
- Avoid dynamic imports from untrusted configuration. Sink selection should go
  through safe registries or explicit entry points with documented behavior.
- Keep generated artifacts out of source control unless they are intentionally
  tracked examples or documentation assets.

## Configuration And Secrets

Configuration is part of the public contract. Treat it with the same care as
code:

- Validate configuration at the boundary and fail with actionable
  `ConfigurationError` messages.
- Prefer environment-variable references for secrets, such as password
  environment variable names, instead of storing secret values in config files.
- Redact secrets in CLI output, logs, exceptions, reports, and test snapshots.
- Do not dump complete process environments, complete connection strings, or
  raw headers that may contain credentials.
- Keep example credentials obviously fake and clearly marked for local
  development only.
- When adding config fields, document defaults, accepted values, security
  impact, and whether changing the value can affect delivery guarantees.

## Security Engineering

Assume `nats-sinks` will be used in critical production systems:

- Treat message payloads, NATS headers, Oracle rows, and DLQ messages as
  potentially sensitive.
- Use allow-list validation for SQL identifiers, sink names, route names, and
  file paths supplied by configuration.
- Use bind variables for database values. Never concatenate user-provided
  values into SQL statements.
- Keep TLS verification enabled by default. Any local-development exception
  must be explicit, documented, and unsuitable for copy-paste production use.
- Prefer least privilege in examples and documentation. Production Oracle users
  should receive only the permissions needed for the configured sink table.
- Do not introduce pickle, unsafe YAML loading, shell interpolation, or unsafe
  deserialization of untrusted data.
- When adding file handling, avoid path traversal, avoid following untrusted
  symlinks for sensitive files, and document expected permissions.
- Keep dependency updates, CodeQL, dependency review, Ruff, typing, Bandit, and
  package checks green.

## Failure Handling

Long-running sink processes must handle non-happy flows gracefully. A malformed
payload, bad route, transient NATS disconnection, Oracle outage, rejected DLQ
publish, unexpected client object, or unexpected sink exception must not create
an early ACK or an unclassified silent failure.

Use framework-defined errors for expected failure categories:

- `TemporarySinkError` for retryable downstream failures.
- `PermanentSinkError` for invalid messages or unrecoverable sink input.
- `SerializationError` and `ValidationError` for bad payloads or message shape.
- `DeadLetterError` when DLQ publication fails before the original ACK.
- `AckError` when durable success happened but JetStream ACK failed.

Boundary-level `except Exception` blocks are acceptable only when they prevent a
service loop from crashing, log safe context, and route the message to redelivery
or a documented error path. Do not catch broad exceptions inside business logic
to hide failures. Never suppress errors silently.

Every failure path should answer three questions in code, tests, and
documentation:

1. Was durable state committed?
2. Was the JetStream message acknowledged, not acknowledged, negatively
   acknowledged, or published to a DLQ?
3. What should an operator do next?

When durable success is uncertain, prefer redelivery. When a permanent failure
is certain and a DLQ is configured, publish to the DLQ first and ACK the
original message only after the DLQ publication succeeds.

## Observability And Logging

Production operations depend on useful, safe signals:

- Log lifecycle transitions, configuration validation failures, retry decisions,
  DLQ decisions, sink start/stop events, and summary counters.
- Keep logs structured enough for ingestion by common log systems, even when
  using the standard `logging` package.
- Never log payloads by default. If payload logging is explicitly enabled,
  document the risk and keep test coverage for redaction behavior.
- Include stable identifiers such as stream, consumer, subject, sequence, batch
  size, and sink type when they are safe to log.
- Avoid high-cardinality or sensitive values in metrics labels.
- Metrics and timing measurements should be best-effort observations; they must
  not affect ACK ordering or durable commit behavior.

## Data And Idempotency

At-least-once delivery means duplicate processing is normal:

- New sinks must define their idempotency model before they are considered
  production-ready.
- Prefer natural durable keys such as JetStream stream plus stream sequence, or
  a validated message ID, over best-effort in-memory duplicate tracking.
- Document duplicate behavior for each write mode. Non-idempotent modes must be
  clearly marked and tested.
- Schema changes must be backwards-compatible where practical. If a migration is
  required, document the operational sequence and rollback considerations.
- Store metadata defensively. Missing optional NATS headers must not crash the
  sink, and future NATS headers should be preserved in metadata snapshots where
  the sink supports metadata persistence.

## Testing Discipline

Every change that touches delivery, idempotency, configuration, parsing, NATS
connection options, SQL generation, or Oracle writes should include tests for
malformed input and failure behavior. Unit tests must be deterministic and must
not depend on NATS, Oracle, the network, or wall-clock timing. Integration tests
may use real services only when guarded by explicit markers and environment
variables.

Before committing production code, run the normal local checks when practical:

```bash
ruff format --check .
ruff check .
mypy src
python -m pytest -q
mkdocs build --strict
python -m build
twine check dist/*
```

Additional testing expectations:

- Add regression tests for every bug fix.
- Cover both success and failure behavior for public functions and CLI commands.
- Use table-driven tests for validators and route matching where possible.
- Use property-style or fuzz-style tests for parsers and normalizers when they
  accept external input.
- Keep live NATS, Oracle, and end-to-end tests behind explicit integration
  markers and environment variables.
- Sanitize test reports before committing. Do not include hostnames, usernames,
  passwords, wallet contents, certificates, tokens, or private payloads.
- Do not mark live integration tests as passed unless they were actually run in
  the current environment.

## Documentation And Comments

Source files should contain meaningful module documentation. Public classes and
functions should have docstrings explaining contracts, especially around ACK
ordering, durable commit boundaries, idempotency, redaction, and Oracle SQL
safety. Comments should explain the reason for safety-sensitive behavior rather
than restating obvious code.

Documentation should be written for external users who may be new to NATS,
JetStream, Oracle, Python packaging, or sink connectors:

- Explain concepts before relying on acronyms or internal terminology.
- Include runnable commands where possible, using fake credentials and
  clearly-marked local-development examples.
- Use diagrams and sequence diagrams for delivery, ACK, DLQ, and sink
  lifecycle behavior when they make the flow easier to understand.
- Keep generic framework documentation separate from sink-specific
  documentation so future sinks can be added without confusing Oracle-specific
  guidance with core behavior.
- Update README, docs pages, examples, and CLI help together when public
  behavior changes.
- Keep the documentation set in a release-ready state. Do not leave new
  behavior documented only in code comments, chat notes, local files, or test
  output.

## Packaging And Release Hygiene

The package should remain PyPI-ready after ordinary development work:

- Keep `pyproject.toml` metadata, optional extras, console scripts, classifiers,
  Ruff configuration, mypy configuration, pytest configuration, and coverage
  configuration coherent.
- Keep `src/` layout and `py.typed` intact so type information is distributed to
  users.
- Ensure README links render correctly on PyPI. README documentation links
  should use fully qualified Read the Docs URLs, while source-code and
  repository links should use fully qualified GitHub URLs.
- Keep `docs/` links version-local with relative Markdown links unless the link
  intentionally points to an external site or repository source path.
- Do not publish from a dirty tree. Release commits should include version,
  changelog, documentation, and test-report updates that match the released
  behavior.
- Before tagging, the `Unreleased` changelog entries should be moved into the
  new version section with the release date, and the documentation should match
  the package version being published.
- Use short-lived authentication and trusted publishing where available. Do not
  commit PyPI tokens, GitHub tokens, signing keys, or local release credentials.
- Release automation should create or update the GitHub Release from the pushed
  tag and attach the built source distribution and wheel.
- Keep GitHub Actions versions current with GitHub-hosted runner runtimes so
  releases do not depend on deprecated Node.js versions.
