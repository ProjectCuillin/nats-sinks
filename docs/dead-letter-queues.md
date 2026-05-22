# Dead Letter Queues

A dead-letter queue, often shortened to DLQ, is a place where a consumer sends
messages that cannot be processed successfully in their current form. DLQs are
useful because they let the main stream continue while preserving failed
messages for inspection, repair, or replay.

In `nats-sinks`, DLQs are used for permanent failures that should not be
retried without changing the message or destination configuration.

In mission-oriented operations, a DLQ is part of the evidence trail. It should
give operators enough context to triage a malformed, unauthorized, or
schema-incompatible event without losing sight of the original processing
failure.

Examples:

- invalid JSON payload,
- missing required idempotency field,
- validation failure,
- non-retryable destination error.

## ACK Rule

> The original message is ACKed only after DLQ publication succeeds.

If DLQ publish fails, the original message remains unacked and eligible for
redelivery. This keeps the failure visible to JetStream rather than silently
discarding a message that still needs operator attention.

## Flow

```mermaid
sequenceDiagram
    participant JS as JetStream
    participant R as Runner
    participant S as Sink
    participant Q as DLQ Subject

    JS->>R: Deliver message
    R->>S: write_batch
    S-->>R: PermanentSinkError
    R->>Q: Publish DLQ JSON
    Q-->>R: Publish success
    R->>JS: ACK original
```

## Payload Shape

The DLQ payload is JSON so operators can inspect it with ordinary tooling. It
can include:

- original subject,
- stream,
- consumer,
- stream sequence,
- consumer sequence,
- message ID,
- redelivery state,
- pending count,
- idempotency key,
- error type and message,
- optional headers,
- optional base64 payload.

Payload inclusion is configurable because source payloads may be sensitive.
For restricted, classified, or otherwise sensitive streams, consider disabling
payload inclusion in the DLQ and relying on message metadata, idempotency keys,
and the original stream retention policy for investigation.

DLQ publish permission is separate from source-subject permission. The sink
runtime account does not need to publish to the source subject, and it does not
need to subscribe to the DLQ subject. When DLQ is enabled, grant publish access
only to the configured `dead_letter.subject`. The complete NATS permission
templates are documented in
[NATS Least-Privilege Permissions](nats-permissions.md).

For a complete operational example of DLQ triage, replay preparation, and
safe public reporting, see
[DLQ Triage And Replay Preparation](use-cases/mission-support/dlq-triage-and-replay.md).
