# ADR 0004: Single Repository, Single Namespace

## Status

Accepted.

## Decision

Use GitHub repository `nats-sinks`, PyPI package `nats-sinks`, import namespace `nats_sinks`, and CLI command `nats-sink`.

## Consequences

Future sinks should live under the same namespace, such as `nats_sinks.postgres` and `nats_sinks.http`, when they are production-ready or clearly marked experimental.
