# ADR 0003: Oracle First Sink

## Status

Accepted.

## Decision

Oracle Database is the first production sink because it exercises durable transactions, idempotency, SQL safety, and operational failure handling.

## Consequences

The Oracle sink establishes the production-quality bar for future sinks.
