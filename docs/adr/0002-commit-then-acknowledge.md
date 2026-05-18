# ADR 0002: Commit Then Acknowledge

## Status

Accepted.

## Decision

JetStream messages are ACKed only after durable sink success.

## Consequences

The system provides at-least-once delivery and must rely on idempotent sink behavior for redelivery. It does not claim exactly-once processing.
