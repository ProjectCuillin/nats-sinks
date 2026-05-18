# ADR 0001: Core Owns Delivery Semantics

## Status

Accepted.

## Decision

The core runtime owns NATS connectivity, batching, retry classification, DLQ behavior, and ACK behavior. Sinks own destination writes only.

## Consequences

Sinks receive `NatsEnvelope` objects and must never receive raw NATS messages. This prevents destination modules from acknowledging messages before durable success.
