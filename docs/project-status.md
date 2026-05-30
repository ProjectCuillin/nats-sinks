# Project Status

The current public release is `0.4.2`. Active development work is staged for
`v0.4.3` on the release branch until the maintainer decides to release it.

`nats-sinks` is pre-1.0 software, but the core delivery model is intentionally
conservative: commit first, ACK last, and design every sink for safe
redelivery. The project does not claim exactly-once delivery. It provides
at-least-once delivery with clear commit ordering and idempotent sink support.

## Production-Ready Areas

The following areas are intended for production-oriented use when configured
with least-privilege identities, secure deployment settings, and appropriate
operator testing:

- core JetStream pull-consumer runtime;
- commit-then-acknowledge processing;
- immutable `NatsEnvelope` abstraction;
- JSON configuration and redacted effective-config output;
- CLI validation and sink test commands;
- Oracle Database sink;
- Oracle MySQL sink;
- File sink;
- Edge Spool sink;
- HTTP sink;
- S3-compatible object sink;
- payload encryption;
- message authenticity verification;
- tamper-evident custody metadata;
- priority, classification, labels, security labels, and mission metadata
  handling;
- metrics snapshots and local metrics inspection.

Production-ready does not mean accredited for a specific environment. Operators
must still perform their own security review, performance validation, database
schema review, deployment hardening, and release acceptance.

## Experimental Or Certification-Stage Areas

The following areas are available for evaluation and focused testing, but
should not be described as production-certified unless their dedicated
documentation has accepted evidence for the intended deployment mode:

- Oracle NoSQL Database sink;
- Oracle Coherence Community Edition sink;
- Palantir Foundry sink;
- Palantir Gotham sink;
- future external connector discovery;
- live platform-specific certification harnesses that require protected
  credentials or non-public systems.

Experimental sinks still follow the project safety model. They must not ACK
before required durable work succeeds, and their tests must prove duplicate
handling and fail-closed behavior before production certification is claimed.

## Release Workflow

Normal development happens on a release branch. Individual issues use issue
branches, and release branches merge into `main` only when the maintainer
explicitly performs a release. GitHub Actions are kept quiet for ordinary
branch work and are used for release validation, release publication,
documentation publication, and selected governance checks.

The live backlog is tracked in GitHub Issues. `CHANGELOG.md` is the shipped
release history. Issues marked `completed` are done in development but remain
open until the release that contains them has actually been published.

## Delivery Guarantee

The supported delivery model is at-least-once. If the sink fails before durable
commit, the runner does not ACK. If durable commit succeeds but the process
exits before ACK, JetStream may redeliver. That is expected. Sinks therefore
need deterministic idempotency keys and duplicate-safe write modes.

The safest duplicate is one the destination can recognize. Silent loss after
an early ACK is the failure mode this project is designed to avoid.
