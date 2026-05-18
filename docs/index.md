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

## What The Package Provides

`nats-sinks` is a Python framework for outbound JetStream consumers. It focuses on a narrow but important responsibility: moving messages from NATS JetStream into durable destinations without acknowledging messages too early.

The package includes:

- a pull-based JetStream runner,
- an immutable `NatsEnvelope` abstraction,
- a small destination sink protocol,
- JSON configuration loading and redacted output,
- dead-letter queue handling,
- a CLI command named `nats-sink`,
- Oracle Database and local files as production sinks,
- tests and documentation for the commit-then-acknowledge invariant.

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

The current release is `0.2.0`. The project is in the `0.x` phase: it is a
production-ready foundation with Oracle and local file sinks, while the public
API remains intentionally small so it can stabilize before `1.0.0`.

Future sinks will be added only when they can satisfy the same delivery, idempotency, security, and test requirements.

## Where To Start

- Read [Getting Started](getting-started.md) for a local run.
- Read [Architecture](architecture.md) to understand the runtime boundary.
- Read [Commit Then ACK](commit-then-ack.md) before implementing any sink.
- Read [Oracle Sink](oracle-sink.md) for table design, modes, and transactions.
- Read [File Sink](file-sink.md) for local file output, atomic writes, and
  duplicate handling.
- Read [Security](security.md) before deploying with real credentials or payloads.
