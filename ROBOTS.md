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
- Maintain public API stability.
- Keep security, typing, linting, packaging, and tests green.
- Treat `commit-then-acknowledge` as a non-negotiable invariant.
- Document public code and add comments around safety-sensitive or non-obvious logic.

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
```

## Documentation And Comments

Source files should contain meaningful module documentation. Public classes and
functions should have docstrings explaining contracts, especially around ACK
ordering, durable commit boundaries, idempotency, redaction, and Oracle SQL
safety. Comments should explain the reason for safety-sensitive behavior rather
than restating obvious code.
