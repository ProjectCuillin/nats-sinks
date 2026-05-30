# Available Today

`nats-sinks` provides a production-oriented runtime for moving messages from
NATS JetStream into durable destinations. The core contract is intentionally
small: receive a JetStream message, normalize it into a `NatsEnvelope`, call a
configured sink, commit the destination write, and ACK only after durable
success.

This page holds the longer capability inventory that used to live in the main
README. The README now stays short so new readers can understand the project
quickly and then follow links into the detailed documentation.

## Runtime And Core Behavior

Available runtime capabilities include:

- default pull-based JetStream consumption through `JetStreamSinkRunner`;
- optional bounded manual-ACK push-consumer mode;
- commit-then-acknowledge processing;
- redelivery-safe error handling;
- DLQ-before-ACK behavior for permanent failures where DLQ is configured;
- graceful shutdown;
- bounded batches and in-flight controls;
- optional server-confirmed ACK handling after durable sink or DLQ success;
- optional JetStream `InProgress` heartbeats for long-running sink writes;
- reconnect tuning for clustered or controlled-network NATS deployments;
- explicit durable consumer management with `bind_only`, `create_if_missing`,
  and `reconcile` modes;
- richer durable consumer policy controls such as plural filter subjects,
  BackOff, MaxDeliver, MaxAckPending, MaxWaiting, headers-only state, replicas,
  memory-storage state, and bounded consumer metadata;
- headers-only payload-presence metadata so producer-empty payloads remain
  distinct from bodies intentionally omitted by JetStream.

`NatsEnvelope` is the immutable internal representation passed to sinks. It
contains payload bytes, headers, JetStream metadata, timestamps, idempotency
metadata, normalized priority, classification, labels, optional security label
profiles, and optional mission metadata. Sinks receive envelopes, not raw NATS
client messages, and sinks never ACK messages.

## Production Sinks

The production sink surface currently includes:

| Sink | Module | Notes |
| --- | --- | --- |
| Oracle Database | `nats_sinks.oracle` | Connection pooling, Oracle Autonomous Database options, idempotent `merge` and `insert_ignore` modes, subject-to-table routing, metadata persistence, and transaction commit before ACK. |
| Oracle MySQL | `nats_sinks.mysql` | Connection pooling, TLS CA support, idempotent `upsert` and `insert_ignore` modes, subject-to-table routing, metadata persistence, payload normalization, and transaction commit before ACK. |
| File | `nats_sinks.file` | Atomic local JSON placement, deterministic filenames, duplicate handling, optional `fsync`, optional gzip compression, and the shared payload normalization contract. |
| Edge Spool | `nats_sinks.spool` | Encrypted disconnected custody, bounded local storage, deterministic idempotency, priority-aware replay, and forwarding into a final destination sink. |
| HTTP | `nats_sinks.http` | Fixed endpoint forwarding, HTTPS-by-default validation, idempotency-key propagation, bounded request/response handling, and retry safety guidance. |
| S3-Compatible Object Storage | `nats_sinks.s3` | Deterministic object keys, conditional duplicate handling, optional metadata sidecars, optional gzip compression, bounded retries, and least-privilege object-storage guidance. |

## Experimental And Certification-Stage Sinks

Experimental sinks are available for evaluation and local contract testing. Do
not treat them as production-certified until the relevant sink page explicitly
says so.

| Sink | Module | Notes |
| --- | --- | --- |
| Oracle NoSQL Database | `nats_sinks.oracle_nosql` | Stores one complete normalized event JSON object in a configured JSON value field, with deterministic K/V-style keys and SDK-backed live test gating. |
| Oracle Coherence Community Edition | `nats_sinks.coherence` | Stores one complete normalized event JSON object as a configured cache or map value, with deterministic keys and container-backed local e2e testing. |
| Palantir Foundry | `nats_sinks.foundry` | Experimental Foundry Streams sink using a narrow HTTP client boundary and fake-client contract tests before any live certification claim. |
| Palantir Gotham | `nats_sinks.gotham` | Experimental Gotham RevDB object sink using a narrow HTTP client boundary and fake-client contract tests before any live certification claim. |

## Configuration And Routing

Runtime configuration is JSON-only. Common sections such as `nats`,
`delivery`, `dead_letter`, `logging`, `metrics`, `message_metadata`,
`security_labels`, `mission_metadata`, `policy`, and `encryption` are shared
across sinks. The `sink` object selects the active destination. The optional
`sinks` object declares named destination instances that can be used by routing
and fan-out policies.

Available routing and sink-composition behavior includes:

- a safe sink connector framework with stable `SinkConnector` metadata;
- explicit `SinkRegistry` resolution;
- disabled-by-default allow-listed entry-point discovery for reviewed external
  connectors;
- named multi-sink configuration for several Oracle Database, Oracle MySQL,
  Oracle Coherence Community Edition, file, spool, HTTP, or S3 sink instances;
- route-match policies that can match normalized subject, priority,
  classification, labels, and approved non-secret headers;
- an opt-in `fanout` sink that writes one message to one or more named child
  sinks and returns success only after every required target has durably
  completed;
- optional fan-out targets with bounded wait controls for side copies that
  should not delay the primary ACK path indefinitely.

## Security And Data Handling

Core data-handling features available today include:

- optional AES-256-GCM and AES-256-CCM payload encryption before envelopes are
  delivered to sinks;
- multi-key payload decryption helper for controlled key rotation, replay, and
  verification workflows;
- optional message authenticity verification with HMAC-SHA256 or Ed25519
  before any sink sees the message;
- optional tamper-evident custody metadata with deterministic payload,
  metadata, and record hashes;
- core-normalized `priority`, `classification`, and `labels` fields;
- optional data-centric security label profiles for releasability, handling
  caveats, owner, originator, policy identifiers, and retention categories;
- optional mission metadata JSON that is validated at the core boundary and
  passed to all current and future sinks;
- optional pre-sink policy enforcement that can require metadata, labels,
  mission metadata, encryption, and payload size limits before destination
  writes;
- optional size policy enforcement for sink-bound payload bytes, headers,
  labels, mission metadata, standard metadata, approximate record size, and
  accepted batch size.

Security features do not turn at-least-once delivery into exactly-once
delivery. Operators should still configure idempotent sink modes and
least-privilege destination identities.

## CLI And Observability

Available command-line tools include:

- `nats-sink` for configuration validation, redacted effective configuration,
  sink health checks, ordered inspection, Oracle lineage queries, stream
  planning, and running sink workers;
- `nats-sink-metrics` for reading a local JSON metrics snapshot and rendering
  tables, JSON, JSONL, shell variables, metric names, or Prometheus text;
- `nats-sink-observe` for disabled-by-default observability policy generation,
  policy validation, Prometheus textfile output, optional native Prometheus
  HTTP scraping, OTLP/HTTP JSON, OCI Monitoring, Amazon CloudWatch, Azure
  Monitor, StatsD, Datadog, Splunk HEC, Elastic, Grafana Alloy, syslog, and
  NATS server monitoring connectors.

External observability sharing is policy controlled and disabled by default.
The sink runner writes local metrics snapshots; connector commands decide what
can be shared with external systems.

## Deployment And Testing Assets

Available operational assets include:

- Kubernetes deployment examples with JSON ConfigMaps, Secret references,
  mounted trust material, restrictive security contexts, resource limits,
  graceful termination, and optional Prometheus observability sidecars;
- a local Oracle Linux 9 slim based Docker image and JSON Compose stack for
  developer smoke testing with temporary NATS JetStream and the file sink;
- a local Oracle MySQL test database container based on Oracle Linux 9 slim
  and Oracle MySQL 9.7.0 LTS;
- a local Oracle NoSQL Database KVLite test backend using Oracle's documented
  Community Edition image from GitHub Container Registry;
- a local Oracle Coherence Community Edition test backend based on Oracle
  Linux 9 slim;
- production container hardening guidance for the Oracle Linux slim image;
- CycloneDX SBOM generation and SHA-256 release checksum manifests;
- hash-verified installation guidance;
- synthetic mission scenario generation for deterministic local evidence;
- container-backed e2e gates for Oracle NoSQL Database and Oracle Coherence
  Community Edition sink testing.

## Use-Case Documentation

The use-case area shows how generic capabilities such as commit-then-ACK,
mission metadata, classification, labels, payload encryption, Oracle storage,
and file output can support operational patterns without making the project a
single-use-case platform.

Current examples include mission-support operational examples, F2T2EA metadata
tagging, sensor event custody, classification and labels, chain of custody,
cross-domain handoff preparation, disconnected edge operation, audit-oriented
persistence, and conceptual defence blueprints for authorized Link 16 /
TADIL-J J-series message events and LOGFAS-related logistics events.
