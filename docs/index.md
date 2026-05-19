# nats-sinks

`nats-sinks` provides at-least-once delivery from JetStream to external destinations with commit-then-acknowledge processing and idempotent sink support.

The project repository is [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/). The current named contributor is Johan Louwers, reachable at [louwersj@gmail.com](mailto:louwersj@gmail.com).

If you are new to this space, NATS is a messaging system that lets services
publish and receive messages using subject names such as `orders.created`.
JetStream is the NATS persistence layer. It stores messages in streams and
redelivers them when a consumer has not acknowledged successful processing.
`nats-sinks` is the bridge between JetStream and external destinations: it
receives JetStream messages, asks a sink to write them durably, and only then
acknowledges the messages back to NATS.

## Documentation Sites

The project publishes documentation in two places:

- [Read the Docs](https://nats-sinks.readthedocs.io/en/latest/) is the primary
  public documentation site for package users. It is intended to host `latest`
  and release-tag documentation.
- [GitHub Pages](https://projectcuillin.github.io/nats-sinks/) is a
  repository-hosted mirror of the current `main` branch documentation.

Use Read the Docs when you need documentation that matches an installed package
version. Use GitHub Pages when you want to inspect the current state of the
repository documentation after it has been published from `main`.

## Available Today

`nats-sinks` is a Python framework for outbound JetStream consumers. It focuses on a narrow but important responsibility: moving messages from NATS JetStream into durable destinations without acknowledging messages too early.

The current release provides the following production-ready foundation:

- `JetStreamSinkRunner`, a pull-based JetStream runtime with bounded batches,
  backpressure controls, graceful shutdown, DLQ support, safe ACK behavior, and
  clear error handling.
- `NatsEnvelope`, an immutable message representation that gives sinks payload,
  headers, JetStream metadata, timestamps, and idempotency keys without giving
  them ACK methods.
- JSON configuration loading with environment-variable overrides and redacted
  output for secrets.
- A CLI command named `nats-sink` for validation, redacted effective config,
  sink health checks, and running sink processes.
- `nats_sinks.oracle.OracleSink`, a production Oracle Database sink with
  connection pooling, idempotent `merge` and `insert_ignore` modes, Oracle
  Autonomous Database connection options, subject-to-table routing, metadata
  persistence, and transaction commit before ACK.
- `nats_sinks.file.FileSink`, a production local file sink with atomic JSON
  file placement, deterministic filenames, duplicate handling, optional gzip
  compression, metadata persistence, and the same payload normalization contract
  used by Oracle.
- Tests and documentation for the commit-then-acknowledge invariant across the
  core runtime and both production sinks.

## Production Sinks

| Sink | Import | Main Use Case | Durable Success Boundary |
| --- | --- | --- | --- |
| Oracle Database | `from nats_sinks.oracle import OracleSink` | Persist JetStream messages into Oracle tables with idempotent writes. | Oracle transaction committed. |
| Local files | `from nats_sinks.file import FileSink` | Write one JSON or gzip-compressed JSON document per message for local handoff, audit, development, or simple archival flows. | Final file atomically placed after flush and optional `fsync`. |

Both sinks follow the same framework rule: the sink writes durably and returns
success; the core runner ACKs JetStream messages afterward.

## High-Level Flow

```mermaid
flowchart LR
    P[Publisher] --> S[JetStream stream]
    S --> C[Durable pull consumer]
    C --> R[nats-sinks runner]
    R --> E[NatsEnvelope batch]
    E --> W[sink.write_batch]
    W --> D[Durable destination]
    D --> A[Commit complete]
    A --> ACK[JetStream ACK]
```

## Package Status

The current release is `0.2.1`. The project is in the `0.x` phase: it is a
production-ready foundation with Oracle and local file sinks, while the public
API remains intentionally small so it can stabilize before `1.0.0`.

## Where To Start

- Read [Getting Started](getting-started.md) for a local run.
- Read [Architecture](architecture.md) to understand the runtime boundary.
- Read [Commit Then ACK](commit-then-ack.md) before implementing any sink.
- Read [Oracle Sink](oracle-sink.md) for table design, modes, and transactions.
- Read [File Sink](file-sink.md) for local file output, atomic writes, and
  duplicate handling.
- Read [Security](security.md) before deploying with real credentials or payloads.

## What Is Next

Future work is kept at the end of this page so readers first see what the
package can do now. Planned capabilities are not production features until they
are implemented, tested, documented, and released.

Planned areas include:

- broader metrics and observability,
- additional idempotency strategies,
- Postgres, HTTP, S3, Kafka, and other sink modules,
- Docker and Kubernetes deployment assets,
- more certified NATS authentication options,
- more JetStream consumer tuning options,
- sink certification tests for future backends.

Read the [Roadmap](roadmap.md) and [NATS Feature Gap Analysis](nats-feature-gap-analysis.md)
for the full list.
