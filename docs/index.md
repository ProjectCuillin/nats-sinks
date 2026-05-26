# nats-sinks

[![PyPI](https://img.shields.io/pypi/v/nats-sinks?cacheSeconds=300)](https://pypi.org/project/nats-sinks/)
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
acknowledges the messages back to NATS. Its first production database target is
Oracle Database, including Oracle Autonomous Database on Oracle Cloud
Infrastructure (OCI), while the framework remains open for additional durable
backends.

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
  optional data-centric security labels, timestamps, and idempotency keys
  without giving them ACK methods.
- JSON configuration loading with environment-variable overrides and redacted
  output for secrets.
- Optional core payload encryption with AES-256-GCM and AES-256-CCM before
  messages are written by any sink.
- A public multi-key payload decryption helper for controlled key-rotation,
  replay, migration, and verification workflows.
- Optional message authenticity verification that checks HMAC-SHA256 or
  Ed25519 signatures before Oracle, file, spool, or future sinks see a
  message. This complements NATS authentication and TLS by proving that the
  message body and selected metadata were signed by an approved producer.
- Optional tamper-evident custody metadata with deterministic payload,
  metadata, and record hashes computed before sink writes. This is evidence
  support for later verification, not encryption or a digital signature.
- Optional data-centric security label profiles for structured releasability,
  handling caveats, owner, originator, policy identifiers, and retention
  categories. The profile is metadata only and does not replace destination
  authorization.
- Optional pre-sink policy enforcement that runs after message normalization,
  metadata defaults, mission metadata validation, and payload encryption, but
  before Oracle, file, or future sink writes. It can require priority,
  classification, labels, mission metadata, encrypted payloads, and payload
  size limits by subject. Policy rejections never reach a sink and follow the
  DLQ-before-ACK rule when DLQ is configured.
- Optional core size policy enforcement that can bound sink-bound payload
  bytes, normalized headers, labels, mission metadata, standard metadata,
  approximate record size, and accepted batch size before any sink write.
- A CLI command named `nats-sink` for validation, redacted effective config,
  sink health checks, and running sink processes.
- A companion CLI command named `nats-sink-metrics` for inspecting local JSON
  metrics snapshots as tables, JSON, JSONL, shell variables, metric names, or
  Prometheus text output.
- A companion CLI command named `nats-sink-observe` for generating disabled
  observability policies, reviewing metric and subject sharing, and writing
  policy-filtered Prometheus textfiles for node_exporter or running the
  optional native Prometheus HTTP endpoint. It can also export approved
  metrics to an OpenTelemetry Collector through OTLP/HTTP JSON.
- Basic metrics counters and timing observations for fetched, prepared,
  written, ACKed, NAKed, failed, DLQ, sink write, ACK error, and active batch
  behavior. The built-in runner can write a local JSON snapshot when
  configured, Oracle duplicate/conflict counters are readable through the same
  snapshot and CLI, and external observability sharing is controlled by a
  separate policy that is disabled by default for Prometheus textfile,
  Prometheus HTTP, OTLP, and NATS monitoring connectors.
- Optional JetStream advisory observation for selected advisory subjects, with
  aggregate counters for delivery and cluster signals while keeping advisory
  payloads and subject details out of exported metrics by default.
- Explicit durable pull-consumer management with safe startup drift detection,
  including `bind_only`, `create_if_missing`, and `reconcile` modes for
  controlled NATS operations.
- Rich durable pull-consumer policy controls for plural filter subjects,
  server-side BackOff, MaxDeliver, MaxAckPending, MaxWaiting, headers-only
  state, replicas, memory-storage state, and bounded consumer metadata.
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
- [Production container hardening](container-hardening.md) for the Oracle
  Linux slim image, including non-root UID/GID `10001`,
  read-only-root-compatible runtime guidance, explicit writable paths, OCI
  labels, SBOM and vulnerability-scanning expectations, and careful defence
  accreditation caveats.
- [Oracle MySQL test database container](oracle-mysql-test-container.md), a
  local Oracle Linux 9 slim and Oracle MySQL 9.7.0 LTS test target for Oracle
  MySQL sink development and e2e testing, with random per-run credentials,
  loopback-only exposure, cleanup by default, and deterministic unit tests.
- `nats_sinks.oracle.OracleSink`, a production Oracle Database sink with
  connection pooling, idempotent `merge` and `insert_ignore` modes, Oracle
  Autonomous Database connection options for OCI deployments,
  subject-to-table routing, metadata persistence, and transaction commit before
  ACK.
- `nats_sinks.mysql.MySqlSink`, a production Oracle MySQL sink with connection
  pooling, TLS CA support, idempotent `upsert` and `insert_ignore` modes,
  subject-to-table routing, metadata persistence, payload normalization, and
  transaction commit before ACK.
- `nats_sinks.file.FileSink`, a production local file sink with atomic JSON
  file placement, deterministic filenames, duplicate handling, optional gzip
  compression, metadata persistence, and the same payload normalization contract
  used by Oracle.
- `nats_sinks.spool.SpoolSink`, a production-oriented encrypted edge spool sink
  for disconnected operation, bounded local custody, deterministic idempotency,
  priority-aware replay, and forwarding into a final destination sink.
- A safe sink connector framework with first-party Oracle Database,
  Oracle MySQL, file, and spool connector descriptors, explicit registry
  resolution, public connector metadata, and
  disabled-by-default allow-listed entry-point discovery for reviewed external
  connectors.
- A named multi-sink configuration registry that lets one JSON file declare
  several Oracle Database, Oracle MySQL, file, or spool sink instances for
  route validation, redacted review, named health checks, and future fan-out
  execution.
- A disabled-by-default generic route-match policy selector that can match
  normalized subject, priority, classification, labels, and approved non-secret
  headers to logical target names and bounded ACK-gating policy for future
  fan-out delivery.
- Tests and documentation for the commit-then-acknowledge invariant across the
  core runtime and both production sinks.

The same features are intentionally useful in mission-oriented deployments:
priority can signal handling urgency, classification can capture the handling
domain, labels can carry operational tags such as sensor family, mission
thread, platform class, exercise identifier, coalition caveat, or audit lane.
Security label profiles can preserve releasability and handling policy context,
while payload encryption can protect stored message bodies while leaving enough
metadata for routing and audit.

## Production Sinks

| Sink | Import | Main Use Case | Durable Success Boundary |
| --- | --- | --- | --- |
| Oracle Database | `from nats_sinks.oracle import OracleSink` | Persist JetStream messages into Oracle tables with idempotent writes. | Oracle transaction committed. |
| Oracle MySQL | `from nats_sinks.mysql import MySqlSink` | Persist JetStream messages into Oracle MySQL tables with idempotent writes. | Oracle MySQL transaction committed. |
| Local files | `from nats_sinks.file import FileSink` | Write one JSON or gzip-compressed JSON document per message for local handoff, audit, development, or simple archival flows. | Final file atomically placed after flush and optional `fsync`. |
| Edge spool | `from nats_sinks.spool import SpoolSink` | Commit encrypted local custody during disconnected operation, then replay later to Oracle, file, or another sink. | Encrypted spool record atomically placed after flush and optional `fsync`. |

All built-in sinks follow the same framework rule: the sink writes durably and
returns success; the core runner ACKs JetStream messages afterward.

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

The current release is `0.4.1`. The project is in the `0.x` phase: it is a
production-ready foundation with Oracle and local file sinks, while the public
API remains intentionally small so it can stabilize before `1.0.0`.

## Where To Start

The documentation is organized as a tree so readers can move from first use to
operations without hunting through a long flat list.

### Start Here

- [Getting Started](getting-started.md): run a local example.
- [Configuration](configuration.md): understand the JSON configuration shape.
- [CLI](cli.md): use `nats-sink`, `nats-sink-metrics`, and
  `nats-sink-observe`.
- [Python Usage](python-usage.md): embed the framework in another Python
  project.

### Core Concepts

- [Architecture](architecture.md): understand the runtime boundary.
- [Commit Then ACK](commit-then-ack.md): learn the non-negotiable delivery
  invariant before implementing or operating any sink.
- [Sink Framework](sink-framework.md): understand how future sinks fit into the
  package without breaking the public API.
- [Configuration](configuration.md#routing): review the generic route-match
  policy selector for future multi-sink routing.
- [Idempotency](idempotency.md), [Dead Letter Queues](dead-letter-queues.md),
  [Message Sizing](message-sizing.md), and [Performance](performance.md):
  understand delivery behavior under duplicate, malformed, large, or high-rate
  conditions.

### NATS

- [NATS Connections](nats-connections.md): configure URLs, authentication, TLS,
  and reconnect behavior.
- [NATS Least-Privilege Permissions](nats-permissions.md): prepare runtime,
  DLQ, management, and advisory-reader accounts.
- [JetStream Stream Management Planning](stream-management.md): generate
  offline retention, discard, storage, replica, duplicate-window, and
  permission-review guidance without giving the sink worker stream-admin
  authority.
- [Advanced JetStream Topology](jetstream-topology.md): review mirrors, sources,
  transforms, republish behavior, placement, compression, metadata, and
  idempotency implications.
- [Headers-Only Delivery Evaluation](headers-only-delivery.md): review the
  design decision for metadata-only JetStream consumers and the follow-up
  backlog items needed before nats-sinks claims explicit headers-only support.
- [NATS Feature Gap Analysis](nats-feature-gap-analysis.md): track what NATS
  supports that nats-sinks does not yet manage directly.

### Sinks

- [Oracle Sink](oracle-sink.md): table design, write modes, staging-table
  merge mode, metadata columns, Autonomous Database, and transactions.
- [Oracle MySQL Sink](mysql-sink.md): Oracle MySQL connection settings, TLS,
  schema design, subject-to-table routing, and container-backed testing.
- [File Sink](file-sink.md): atomic local files, deterministic filenames,
  duplicate handling, gzip compression, and handoff patterns.
- [Named Sinks And Routing](named-sinks.md): declare several destination
  instances in one configuration file and validate route target references.
- [Edge Spool Sink](spool-sink.md): encrypted local custody for disconnected
  operation and controlled replay into a final destination sink.

### Data Handling

- [Message Authenticity](message-authenticity.md): verify producer signatures
  over payload hashes and selected metadata before sink delivery.
- [Payload Encryption](payload-encryption.md): encrypt stored message bodies
  while keeping safe operational metadata available.
- [Priority-Aware Processing Lanes](priority-lanes.md): prefer urgent messages
  inside already-fetched bounded batches without claiming strict total
  ordering.
- [Mission Metadata](mission-metadata.md): add a validated JSON context object
  for mission, operation, platform, source-system, track, confidence,
  releasability, or lifecycle metadata.
- [Configuration](configuration.md#pre_sink_policy): configure the optional
  pre-sink policy gate for destination-neutral validation before sink writes.
- [Message Sizing](message-sizing.md): configure and tune payload, header,
  metadata, label, record, and batch-size guardrails before sink writes.

### Observability

- [Observability](observability.md): start here for the external-sharing model.
- [Metrics Snapshot And CLI](metrics.md): inspect local snapshots and use
  shell/Python-friendly metric output.
- [Prometheus Integration](prometheus.md): configure the policy-controlled
  textfile connector or optional native HTTP endpoint.
- [OpenTelemetry OTLP Integration](otlp.md): export approved metrics to an
  OpenTelemetry Collector through OTLP/HTTP JSON.
- [Elastic Observability Profile](elastic-observability.md): export approved
  metrics through an Elastic-oriented profile over the OTLP connector.
- [Grafana Alloy Profile](grafana-alloy.md): export approved metrics through
  an Alloy-oriented profile over the OTLP connector.
- [Splunk HEC Integration](splunk-hec.md): export approved aggregate metrics to
  Splunk HTTP Event Collector for security operations and incident response.
- [StatsD Integration](statsd.md): export approved aggregate metrics as
  best-effort UDP or Unix datagrams to StatsD-compatible aggregators.
- [Syslog Bridge](syslog.md): export approved aggregate metrics as bounded
  RFC 5424-style messages to syslog pipelines.
- [NATS Server Monitoring](nats-server-monitoring.md): understand why endpoints
  such as `/jsz` and `/healthz` stay outside the delivery worker.
- [Future Observability Connectors](observability-connectors.md): review the
  shared connector contract and the staged connector backlog for Datadog, OCI
  Monitoring, CloudWatch, Azure Monitor, and other future
  connectors.

### Deployment, Security, And Quality

- [Operations](operations.md), [Service Deployment](service-deployment.md), and
  [Kubernetes Deployment](kubernetes.md): deploy and operate the worker and
  optional observability services.
- [Security](security.md), [Security Rule Review](security-rule-review.md),
  [Dependency Management](dependency-management.md),
  [Hash-Verified Installs](hash-verified-installs.md), and [SBOM](sbom.md):
  prepare high-trust deployment and supply-chain workflows.
- [Testing](testing.md), [Sink Certification](sink-certification.md),
  [Latest Test Report](test-report.md), and
  [Public API Compatibility](public-api.md): verify behavior, certify sink
  readiness, and protect the supported import surface.

### Use Cases

- [Use Cases](use-cases/index.md): start here for implementation blueprints
  that combine generic nats-sinks features without turning the framework into a
  one-use-case product.
- [Mission-Support Operational Examples](use-cases/mission-support/index.md):
  restricted event storage, disconnected file handoff, DLQ triage, and
  destination outage recovery.
- [Defence And Mission Support](use-cases/defence/index.md): sensor event
  custody, F2T2EA phase tagging, classification and labels, chain of custody,
  cross-domain handoff preparation, edge operation, audit-oriented persistence,
  and synthetic mission testing.

### Project Workflow

- [Development](development.md), [Backlog Management](backlog-management.md),
  [Branch Workflow](branch-workflow.md), [Read the Docs](read-the-docs.md),
  [GitHub Pages](github-pages.md), [Release](release.md),
  [Publishing Releases](publishing.md), and [Roadmap](roadmap.md): maintain the
  project, publish documentation, and prepare releases.

Read [Security](security.md) before deploying with real credentials or payloads.

## What Is Next

Future work is kept at the end of this page so readers first see what the
package can do now. Planned capabilities are not production features until they
are implemented, tested, documented, and released.

Planned areas include:

- additional idempotency strategies,
- HTTP, S3, Kafka, OCI Object Storage, Oracle MySQL, and other sink modules,
- Docker and Kubernetes deployment assets,
- more live certification runbooks for secure NATS authentication deployments,
- more JetStream consumer tuning options,
- sink certification tests for future backends.

Read the [Roadmap](roadmap.md) and [NATS Feature Gap Analysis](nats-feature-gap-analysis.md)
for the full list.
