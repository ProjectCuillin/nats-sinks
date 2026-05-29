# Headers-Only Delivery

NATS JetStream can deliver a message to a consumer without the original body.
This is called headers-only delivery. It is useful when a workflow needs to
inspect metadata, route an event, or prove that a message existed without
exposing the full payload to the consumer.

This page records the nats-sinks behavior for that capability. Current
releases can create, reconcile, or validate the JetStream `headers_only`
consumer setting through `consumer_management.headers_only`, and the runtime
persists explicit payload-presence metadata so operators can distinguish a
genuinely empty producer payload from a payload that the NATS server
intentionally omitted.

## What NATS Provides

The NATS consumer configuration includes `HeadersOnly`. When enabled, the
consumer receives headers without the message body, and the server adds the
`Nats-Msg-Size` header so the consumer can see the size of the removed payload.
The NATS documentation lists this as a JetStream consumer option introduced in
NATS Server 2.6.2.

The `nats-py` client also exposes `headers_only` on `ConsumerConfig`, which
means Python applications can request this behavior when they create or update
a JetStream consumer. nats-sinks exposes that setting through the validated
`consumer_management` startup block for durable pull consumers.

## Current nats-sinks Behavior

`nats-sinks` converts every message into `NatsEnvelope`. The envelope always
has a `data: bytes` field for backward compatibility. If JetStream omits the
body for headers-only delivery, `data` remains `b""`, but the envelope records
that the original body was not delivered. This is different from a producer
that actually published an empty payload:

- the producer published an empty payload,
- the server omitted a non-empty payload because the consumer is headers-only.

That difference is now visible in standard metadata, DLQ records, file records,
and Oracle metadata JSON. It matters for audit, replay, idempotency, and
destination schemas.

```mermaid
flowchart LR
    P[Producer stores payload in JetStream]
    C{Consumer headers_only?}
    P --> C
    C -->|false| Full[Envelope data contains original bytes]
    C -->|true| HeaderOnly[Envelope data is empty and Nats-Msg-Size is present]
    Full --> Sink[Sink writes payload and metadata]
    HeaderOnly --> Sink
```

## Design Decision

Headers-only delivery is an explicit nats-sinks feature rather than an
accidental side effect of seeing an empty byte string.

The implemented contract is:

1. Use validated consumer configuration for requesting or verifying
   `headers_only`.
2. Keep a destination-neutral payload-presence contract on `NatsEnvelope`.
3. Persist the payload-presence state in sink metadata so operators can see
   whether the original payload was present, empty, or intentionally omitted.
4. Keep ACK ordering unchanged: ACK only after the metadata-only workflow has
   durably succeeded.
5. Build DLQ records carefully: a headers-only consumer cannot include the
   original payload in the DLQ because it never received that payload.

The feature must not imply confidentiality. Headers, subjects, stream names,
sequences, timestamps, priority, classification, labels, mission metadata, and
`Nats-Msg-Size` can still reveal sensitive operational information.

## Envelope Semantics

`NatsEnvelope` exposes explicit payload-presence fields while preserving
backward compatibility for `NatsEnvelope.data`.

| Field | Meaning |
| --- | --- |
| `payload_present` | `true` when the body delivered to nats-sinks is the original message body. |
| `payload_omitted` | `true` when the server intentionally omitted the body for headers-only delivery. |
| `payload_omitted_reason` | A stable reason such as `headers_only`. |
| `original_payload_size_bytes` | Parsed value from `Nats-Msg-Size` when available. |

For normal messages, `payload_present` is `true` and `payload_omitted` is
`false`. For producer-empty messages, `payload_present` is still `true`, `data`
is `b""`, and `original_payload_size_bytes` is `0` when known. For
headers-only messages, `payload_present` is `false`, `payload_omitted` is
`true`, `data` remains `b""` for compatibility, and `original_payload_size_bytes`
comes from `Nats-Msg-Size`.

Malformed or negative `Nats-Msg-Size` values are not guessed or repaired. The
envelope marks `payload_size_header_malformed=true`, keeps
`original_payload_size_bytes=null`, and leaves the rest of the delivery
contract unchanged.

```mermaid
sequenceDiagram
    participant NATS as JetStream
    participant Runner as nats-sinks core
    participant Sink as Destination sink
    participant Store as Durable destination

    NATS->>Runner: Headers-only message with Nats-Msg-Size
    Runner->>Runner: Build envelope with payload_omitted=true
    Runner->>Sink: write_batch(envelopes)
    Sink->>Store: Commit metadata-only record
    Store-->>Sink: Commit success
    Sink-->>Runner: Success
    Runner->>NATS: ACK after durable metadata commit
```

## Idempotency

Stream sequence idempotency remains the recommended mode. A headers-only
consumer still receives JetStream metadata, so `stream + stream_sequence`
continues to identify the source message safely.

Message ID idempotency can also work when `Nats-Msg-Id` is present.

Payload-hash fallback is rejected for headers-only mode. If the
server omits bodies, many different messages may appear to have `b""` as their
payload, which can cause false duplicate detection. When `payload_omitted` is
true and no stream sequence or message ID is available, the envelope raises a
validation error instead of deriving a key from the empty delivered body.

## DLQ Behavior

DLQ behavior is explicit:

- if a headers-only message fails permanently, the DLQ record may include
  headers and metadata,
- it cannot include the original payload unless a different consumer retrieves
  the full message,
- the DLQ payload records that the original body was intentionally
  omitted and include `Nats-Msg-Size` when present,
- the original message must only be ACKed after DLQ publication succeeds.

This keeps the existing safety rule intact:

> Prefer redelivery or explicit DLQ custody over pretending that omitted data
> was stored.

## Sink Storage Impact

Oracle and file sinks store payload-presence metadata in the standard metadata
document. Future schema work can still add optional dedicated columns for
operators who query metadata-only custody records frequently.

Recommended metadata shape:

```json
{
  "payload": {
    "present": false,
    "omitted": true,
    "omitted_reason": "headers_only",
    "original_size_bytes": 4096,
    "delivered_size_bytes": 0,
    "nats_msg_size_header": "4096",
    "nats_msg_size_header_malformed": false
  }
}
```

The normal payload field should not be filled with a fake placeholder that
looks like producer data. If the DLQ builder is configured to include payloads
and the body was omitted, the DLQ record emits `payload_unavailable_reason`
instead of `payload_base64`.

## Security Notes

Headers-only delivery can reduce payload exposure to the sink process, but it
does not make the workflow non-sensitive.

Operators must still protect:

- NATS subjects,
- all NATS headers,
- `Nats-Msg-Size`,
- JetStream stream and sequence values,
- message timestamps,
- priority, classification, and labels,
- mission metadata,
- DLQ records,
- database rows and files that record custody metadata.

The feature should be disabled by default and documented as a deliberate
metadata-only custody mode.

## Supported Scope

The current supported scope covers durable pull consumers managed through
`consumer_management.headers_only`, payload-presence metadata on the envelope,
standard sink metadata, DLQ payloads, and focused commit-then-ACK tests. It
does not recover the omitted body from JetStream. Operators that need the full
payload later should retain the source stream long enough for a separate,
authorized full-payload replay or investigation workflow.
