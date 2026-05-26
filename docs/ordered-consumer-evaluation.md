# Ordered Consumer Evaluation

This page records the evaluation for possible ordered-consumer support in
`nats-sinks`. It is written for operators and maintainers who need to inspect
or replay stream content without weakening the production sink worker contract.

The conclusion is intentionally strict:

- ordered consumers are useful for inspection and analysis,
- ordered consumers must not replace durable pull consumers for production sink
  workers,
- any future feature should be read-only by default and clearly named as
  inspection tooling,
- replaying into sinks should use durable pull consumers with
  commit-then-acknowledge, not ordered inspection consumers.

The current implementation adds a read-only `nats-sink inspect-ordered`
command. It keeps ordered consumers in the inspection lane only: no sink is
constructed, no destination write is attempted, and production durable sink
workers continue to use the normal pull-consumer path.

## Background

The NATS consumer documentation describes ordered consumers as a convenient
form of consumer for efficient stream inspection or analysis. It also describes
important boundaries: ordered consumers are ephemeral, single-threaded, not
load-balanced, and are designed to prevent gaps by client-side sequence
tracking and recreation. See
[JetStream Consumers](https://docs.nats.io/nats-concepts/jetstream/consumers)
and
[Consumer Details](https://docs.nats.io/using-nats/developer/develop_jetstream/consumers).

The same NATS documentation recommends pull consumers for new projects when
scalability, detailed flow control, or error handling matters. That is why the
production `nats-sinks` runner uses a durable pull-consumer model for sink
writes.

## Current Production Path

The production runner uses a durable pull consumer. The durable consumer keeps
server-side progress, the runner owns ACK decisions, and every sink write must
complete durably before the runner ACKs.

```mermaid
sequenceDiagram
    participant JS as JetStream
    participant C as Durable pull consumer
    participant R as nats-sinks runner
    participant S as Sink
    participant D as Durable destination

    JS->>C: Store stream messages
    R->>C: Pull bounded batch
    C-->>R: Deliver messages
    R->>S: write_batch(envelopes)
    S->>D: Write and commit
    D-->>S: Commit success
    S-->>R: Success
    R->>C: ACK after durable success
```

This path remains the production default because it supports at-least-once
delivery, controlled batching, redelivery, DLQ behavior, idempotent sinks, and
graceful shutdown.

## Ordered Consumer Inspection Path

An ordered inspection tool would have a different purpose. It would let an
operator read stream content in order without advancing the production durable
consumer and without writing to sinks.

```mermaid
sequenceDiagram
    participant JS as JetStream
    participant O as Ordered consumer
    participant CLI as Inspection CLI
    participant Out as Redacted local output

    JS->>O: Ephemeral ordered view
    O-->>CLI: In-order messages
    CLI->>CLI: Apply count, byte, and redaction limits
    CLI->>Out: Write inspection report
```

This is operationally useful, but it is not production sink processing. It
should be read-only by default, bounded, redacted, and explicitly named so
users do not confuse it with durable replay or sink delivery.

The CLI command is:

```bash
nats-sink inspect-ordered examples/file-basic/config.json \
  --max-messages 5 \
  --max-payload-bytes 1048576
```

It fails closed when the installed NATS Python client does not expose the
`ordered_consumer` subscribe option. This prevents a misleading fallback to an
ordinary push or durable pull subscription.

## Why Ordered Consumers Should Not Replace The Sink Runner

Ordered consumers are not a substitute for the production runner because:

- they are ephemeral rather than durable production checkpoints,
- they are intended for inspection or analysis rather than horizontally scaled
  sink workers,
- they do not provide the same operational model for durable write success,
  retry, DLQ, and final ACK,
- they may recreate underlying consumers to recover sequence continuity,
  which is useful for inspection but not the same as sink idempotency,
- they can expose sensitive payloads and metadata if used casually.

The sink runner should continue to use durable pull consumers for production
destination writes.

## Replay To Sinks

If an operator needs to replay historical events into Oracle, local files, or a
future sink, the safer design is a durable replay workflow, not an ordered
inspection consumer.

```mermaid
flowchart TD
    Plan[Replay plan] --> Scope[Bound stream, subject, sequence or time]
    Scope --> DryRun[Dry-run and idempotency review]
    DryRun --> Durable[Create or bind durable pull consumer]
    Durable --> Runner[nats-sinks runner]
    Runner --> Commit[Sink durable commit]
    Commit --> Ack[ACK after durable success]
```

Durable replay-to-sinks should require:

- explicit stream and subject scope,
- explicit start sequence or start time,
- maximum message count or stopping condition,
- sink-specific idempotency review,
- dry-run validation,
- redacted reporting,
- least-privilege NATS permissions,
- commit-then-acknowledge tests.

## Python Client Consideration

Ordered-consumer support depends on the NATS Python client exposing a stable
public API for the feature. The implementation checks the active
`JetStreamContext.subscribe` API for an `ordered_consumer` option before
subscribing. If the option is unavailable or ambiguous, inspection stops with a
configuration error instead of silently falling back to another delivery mode.

## Security Guidance

Inspection and replay are sensitive operations. Even without payload output,
subjects, headers, stream names, sequence numbers, timestamps, priority,
classification, labels, and mission metadata can reveal operational context.

The ordered-inspection CLI:

- redact payloads by default,
- hide sensitive headers by default,
- require explicit opt-in for payload output,
- bound message count and byte count,
- write only under approved local output paths,
- avoid printing credentials, connection strings, server locations, and private
  subject families,
- make output clearly non-production and non-release evidence unless sanitized.

Payload output is available only through `--include-payload`. When enabled,
UTF-8 payloads are emitted as text and binary payloads are emitted as Base64.
The command always includes a payload byte count and SHA-256 digest so
operators can compare records without printing the body.

## Recommended Implementation Split

The evaluation recommended three separate follow-up items:

1. Add ordered-consumer client compatibility and fail-closed capability checks.
2. Add a read-only ordered-consumer inspection CLI.
3. Add durable replay-to-sinks guidance and tooling design.

The first two items are now implemented by `nats-sink inspect-ordered`. The
third remains deliberately separate because replay into destinations has
different delivery semantics and must stay on durable pull consumers.

This split prevents a useful inspection feature from accidentally weakening
the durable sink runtime.

## Current Status

The read-only inspection CLI and fail-closed client compatibility check are
implemented. Durable replay-to-sinks remains separate future work and should
continue to use durable pull consumers with commit-then-acknowledge semantics.
