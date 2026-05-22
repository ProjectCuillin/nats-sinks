# nats-sinks

[![PyPI](https://img.shields.io/pypi/v/nats-sinks.svg)](https://pypi.org/project/nats-sinks/)
[![Python Versions](https://img.shields.io/pypi/pyversions/nats-sinks.svg)](https://pypi.org/project/nats-sinks/)
[![Documentation Status](https://readthedocs.org/projects/nats-sinks/badge/?version=latest)](https://nats-sinks.readthedocs.io/en/latest/?badge=latest)
[![GitHub Pages](https://github.com/ProjectCuillin/nats-sinks/actions/workflows/pages.yml/badge.svg)](https://projectcuillin.github.io/nats-sinks/)

`nats-sinks` provides at-least-once delivery from JetStream to external destinations with commit-then-acknowledge processing and idempotent sink support.

The project repository is [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/). The current named contributor is Johan Louwers, reachable at [louwersj@gmail.com](mailto:louwersj@gmail.com).

If you are new to this space, NATS is a messaging system that lets services
publish and receive messages using subject names such as `orders.created`.
JetStream is the NATS persistence layer. It stores messages in streams and
redelivers them when a consumer has not acknowledged successful processing.
`nats-sinks` is the bridge between JetStream and external destinations: it
receives JetStream messages, asks a sink to write them durably, and only then
acknowledges the messages back to NATS.

The project is written for operators and developers who care about reliable
event movement in operational environments. That includes commercial platforms,
public-sector systems, defence logistics, mission telemetry, audit pipelines,
and other settings where a message can represent an operational fact that must
not be silently lost. In sensor-driven warfighting support environments,
`nats-sinks` is best understood as a durable event custody layer around
command-and-control data fabrics, sensor-fusion pipelines, platform telemetry,
weapon-system status events, sensor-to-shooter workflows, and kill-chain or
kill-mesh style coordination messages. It preserves evidence and state for
authorized downstream systems; it is not a targeting system, fire-control
system, weapons-release mechanism, rules-of-engagement engine, or automation
layer for lethal decision-making.

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
  headers, JetStream metadata, normalized priority/classification/labels fields,
  timestamps, and idempotency keys without giving them ACK methods.
- JSON configuration loading with environment-variable overrides and redacted
  output for secrets.
- Optional core payload encryption with AES-256-GCM and AES-256-CCM before
  messages are written by any sink.
- A CLI command named `nats-sink` for validation, redacted effective config,
  sink health checks, and running sink processes.
- A companion CLI command named `nats-sink-metrics` for inspecting local JSON
  metrics snapshots as tables, JSON, JSONL, shell variables, metric names, or
  Prometheus text output.
- A companion CLI command named `nats-sink-observe` for generating disabled
  observability policies, reviewing metric and subject sharing, and writing
  policy-filtered Prometheus textfiles for node_exporter or running the
  optional native Prometheus HTTP endpoint.
- Basic metrics counters and timing observations for fetched, prepared,
  written, ACKed, NAKed, failed, DLQ, sink write, ACK error, and active batch
  behavior. The built-in runner can write a local JSON snapshot when
  configured, Oracle duplicate/conflict counters are readable through the same
  snapshot and CLI, and external observability sharing is controlled by a
  separate policy that is disabled by default for both textfile and native
  HTTP connectors.
- Exponential retry backoff with configurable caps and jitter for retryable
  failures, preserving redelivery safety without creating synchronized retry
  storms during shared outages.
- Optional priority-aware processing lanes that reorder already-fetched
  bounded batches with weighted starvation controls while preserving
  commit-then-ACK behavior.
- CycloneDX SBOM generation, SHA-256 release checksum manifests, and
  [hash-verified installation guidance](hash-verified-installs.md) for
  high-trust deployment workflows.
- [Kubernetes deployment examples](kubernetes.md) with JSON ConfigMaps, Secret
  references, restrictive security contexts, resource limits, graceful
  shutdown settings, and optional Prometheus observability sidecars.
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

The same features are intentionally useful in mission-oriented deployments:
priority can signal handling urgency, classification can capture the handling
domain, labels can carry operational tags such as sensor family, mission
thread, platform class, exercise identifier, coalition caveat, or audit lane,
and payload encryption can protect stored message bodies while leaving enough
metadata for routing and audit.

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

The current release is `0.4.0`. The project is in the `0.x` phase: it is a
production-ready foundation with Oracle and local file sinks, while the public
API remains intentionally small so it can stabilize before `1.0.0`.

## Where To Start

- Read [Getting Started](getting-started.md) for a local run.
- Read [Architecture](architecture.md) to understand the runtime boundary.
- Read [Commit Then ACK](commit-then-ack.md) before implementing any sink.
- Read [Oracle Sink](oracle-sink.md) for table design, modes, and transactions.
- Read [File Sink](file-sink.md) for local file output, atomic writes, and
  duplicate handling.
- Read [Payload Encryption](payload-encryption.md) when stored message bodies
  should be encrypted while metadata remains available for operations.
- Read [Priority-Aware Processing Lanes](priority-lanes.md) when mixed-urgency
  batches should prefer urgent messages without claiming strict total ordering.
- Read [Mission Metadata](mission-metadata.md) when you need a validated JSON
  context object for mission, operation, platform, source-system, track,
  confidence, releasability, or lifecycle metadata.
- Read [Metrics](metrics.md) when you want local snapshot inspection, shell
  scripting examples, Python hooks, or Prometheus text output.
- Read [Observability](observability.md) and [Prometheus Integration](prometheus.md)
  when you want policy-controlled Prometheus export through node_exporter or
  the optional native HTTP endpoint as separate Linux services.
- Read [NATS Server Monitoring](nats-server-monitoring.md) when you need to
  understand why server endpoints such as `/jsz` and `/healthz` stay outside
  the delivery worker.
- Read [NATS Least-Privilege Permissions](nats-permissions.md) when preparing
  production NATS runtime accounts, DLQ publish rights, or advisory-reader
  accounts.
- Read [Advanced JetStream Topology](jetstream-topology.md) when mirrors,
  sources, subject transforms, republish rules, compression, placement, or
  stream metadata are part of the event path.
- Read [Use Cases](use-cases/index.md) when you want blueprints that combine
  generic nats-sinks features for a specific operational context without
  changing the framework into a one-use-case product.
- Read [Mission-Support Operational Examples](use-cases/mission-support/index.md)
  when you want complete patterns for restricted event storage, disconnected
  file handoff, DLQ triage, and destination outage recovery.
- Read [Synthetic Mission Testing](use-cases/defence/synthetic-mission-testing.md)
  when you need repeatable fake mission-style scenarios for release evidence,
  file-sink smoke checks, or future sink certification without live services.
- Read [Sensor Event Custody](use-cases/defence/sensor-event-custody.md),
  [Classification And Labels](use-cases/defence/classification-and-labels.md),
  [Chain Of Custody](use-cases/defence/chain-of-custody.md),
  [Cross-Domain Handoff Preparation](use-cases/defence/cross-domain-handoff-preparation.md),
  [Edge Operation](use-cases/defence/edge-operation.md), and
  [Audit-Oriented Persistence](use-cases/defence/audit-oriented-persistence.md)
  for concrete mission-support blueprint examples based on current generic
  runtime features.
- Read [F2T2EA Event Phase Tagging](use-cases/defence/f2t2ea-event-phase-tagging.md)
  when you need an optional metadata-only lifecycle tagging pattern built on
  the generic mission metadata feature and kept separate from targeting,
  fire-control, and decision automation.
- Read [Security](security.md) before deploying with real credentials or payloads.

## What Is Next

Future work is kept at the end of this page so readers first see what the
package can do now. Planned capabilities are not production features until they
are implemented, tested, documented, and released.

Planned areas include:

- OpenTelemetry metrics connector,
- additional idempotency strategies,
- Postgres, HTTP, S3, Kafka, and other sink modules,
- Docker and Kubernetes deployment assets,
- more certified NATS authentication options,
- more JetStream consumer tuning options,
- sink certification tests for future backends.

Read the [Roadmap](roadmap.md) and [NATS Feature Gap Analysis](nats-feature-gap-analysis.md)
for the full list.
