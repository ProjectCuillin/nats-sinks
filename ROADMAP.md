# Roadmap

Repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

Named contributor: Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

Unrealized roadmap work is also staged as detailed JSON backlog items under
`backlog/items/`. Those files are synchronized to GitHub Issues through the
backlog tooling so implementation work can be discussed, labeled with a target
release, and closed only after the containing release is published.

## Phase 1

- Core runtime.
- Oracle sink.
- File sink.
- Oracle idempotency support with `stream_sequence`, `message_id`, and
  `payload_field` strategies.
- Oracle duplicate-safe production write modes: `merge` and `insert_ignore`.
- File sink idempotency support with deterministic file names and
  `skip_existing` duplicate handling.
- Core payload encryption with AES-256-GCM and AES-256-CCM before sink writes.
- Fail-closed pre-sink policy enforcement for subject-scoped requirements such
  as priority, classification, labels, mission metadata, encrypted payloads,
  approved mission metadata keys, and bounded sink-bound payload size.
- Core message metadata for priority, classification, and labels so
  mission-oriented deployments can preserve operational handling context across
  all production sinks.
- Generic mission metadata support for one validated JSON context object across
  the core runtime, Oracle `MISSION_METADATA_JSON`, file-sink output, and future
  sink contracts.
- Basic metrics counters and observations for fetched, prepared, written,
  ACKed, NAKed, failed, DLQ, sink write, ACK error, and active batch behavior.
- Local JSON metrics snapshots and the `nats-sink-metrics` inspection CLI for
  table, JSON, JSONL, shell, names, and Prometheus text output.
- Observability core with disabled-by-default sharing policies and a
  `nats-sink-observe` CLI for safe connector operation.
- Policy-controlled Prometheus textfile connector for node_exporter, designed
  to run as a separate Linux service from the sink worker.
- Optional native Prometheus HTTP scrape endpoint, designed as a separate
  disabled-by-default observability service that reads policy-filtered local
  metrics snapshots.
- NATS server monitoring diagnostic connector for selected endpoints such as
  `/jsz` and `/healthz`, implemented outside the delivery worker with explicit
  endpoint and field allow lists.
- Kubernetes deployment examples with JSON ConfigMaps, Secret references,
  mounted trust material, security contexts, resource limits, graceful
  shutdown settings, and optional Prometheus observability sidecars.
- Oracle duplicate/conflict metrics for idempotent Oracle operations, readable
  through the same metrics snapshot and `nats-sink-metrics` CLI.
- Multiple NATS seed URLs, reconnect tuning, and connection event metrics for
  clustered or controlled-network deployments.
- Least-privilege NATS permissions templates for runtime workers, DLQ publish
  rights, optional consumer management, and advisory readers.
- Advanced JetStream topology guidance for mirrors, sources, subject
  transforms, republish behavior, stream compression, placement, metadata, and
  idempotency review.
- Exponential retry backoff with jitter controls for retryable failures while
  preserving commit-then-acknowledge behavior.
- CycloneDX SBOM generation as local, CI, and release evidence.
- Deterministic bounded property-style generator tests for subject matching,
  payload normalization, message metadata, mission metadata, and file path
  sanitization.
- Oracle benchmark scripts that report publish, fetch, map, backend write,
  commit, ACK, retry, and shutdown timing separately for non-production
  environments.
- Synthetic load-test profiles for normal, retry, DLQ, shutdown, optional
  encryption-workload, and metrics-snapshot behavior without live services.
- Deterministic synthetic mission scenario harness for core envelope generation
  and local file-sink smoke testing without live services.
- F2T2EA event phase tagging blueprint as metadata-only use-case documentation
  built on the generic mission metadata feature.
- Defence and mission-support use-case blueprint documentation for sensor event
  custody, classification and labels, chain of custody, cross-domain handoff
  preparation, edge operation, and audit-oriented persistence while keeping the
  framework generic.
- Mission-support operational examples for restricted event storage,
  disconnected file handoff, DLQ triage and replay preparation, and
  destination outage recovery.
- Commit-then-acknowledge contract tests proving ACK happens only after sink
  success and DLQ publication succeeds before ACK.
- CLI.
- Documentation.
- Tests.
- PyPI-ready package.

## Phase 2

- OpenTelemetry OTLP metrics connector for deployments using collectors.
- Individual observability connector backlog items for StatsD, Datadog,
  Splunk HEC, Elastic Observability, Grafana Alloy, Oracle Cloud
  Infrastructure Monitoring, Amazon CloudWatch, Azure Monitor, and syslog
  bridges, all following the shared disabled-by-default observability connector
  contract.
- Headers-only JetStream delivery support split into validated consumer
  configuration, payload-presence metadata, and sink or DLQ certification.
- Additional mission-support documentation examples for future operator
  runbooks, deeper replay drills, and sink-specific certification evidence.
- Documented sink certification contract for idempotency, including required
  duplicate-redelivery tests for every new production sink.
- Per-route or per-table Oracle idempotency overrides for deployments where
  different subjects need different durable keys.
- More Oracle duplicate handling controls, such as clearer `merge`
  update-column controls and deeper merge insert-versus-match visibility where
  Oracle execution metadata can support it reliably.
- HTTP sink idempotency-key support, retry safety guidance, and clear warnings
  for endpoints that cannot provide idempotent semantics.
- S3 sink design with atomic object keys and safe duplicate overwrite/skip
  behavior.
- Native Oracle Cloud Infrastructure Object Storage sink design with
  deterministic object keys, OCI identity support, checksums, multipart upload,
  and least-privilege bucket guidance.
- Oracle MySQL sink design for MySQL and MySQL HeatWave deployments, including
  Connector/Python evaluation, transaction commit timing, idempotent upserts,
  TLS verification, and least-privilege account guidance.
- HTTP sink.
- Docker image.
- Optional dedicated secret-manager connectors for encryption keys when a
  future release can keep provider dependencies isolated behind extras.
- Expanded property-based or dedicated fuzz tooling if the deterministic
  bounded generator suite reaches its limits or future parsers become more
  complex.
- Expanded metadata-size and schema policy controls for deployments that need
  stricter mission metadata validation than the current root-key allow list.
- Hash-verified installation guidance for high-trust environments.
- Certified TLS certificate authentication guidance.
- Certified NKEY with challenge authentication support.
- Certified decentralized JWT authentication/authorization support.
- Certified NATS credentials-file workflows.
- Explicit JetStream consumer creation and reconciliation.
- Configurable consumer `AckWait`, `MaxDeliver`, `BackOff`, and `MaxAckPending`.
- Configurable consumer deliver policies.
- Multiple JetStream filter subjects.
- Optional `AckSync` / double-ACK support after durable sink success.
- Optional `InProgress` handling for long-running sink writes.
- JetStream advisory consumption for operational events and max-deliver signals.
- Richer observability policies for bounded subject-aware metrics, if future
  operators need per-subject insight and can approve the information-sharing
  risk.

## Phase 3

- Plugin discovery.
- Sink certification tests.
- Helm chart.
- Advanced observability.
- WebSocket connection support and documentation.
- Push-consumer support where it can preserve commit-then-acknowledge semantics.
- Ordered-consumer support for inspection or replay workflows, clearly separated
  from production durable sink processing.
- Consumer metadata, replicas, and memory-storage options.
- Headers-only delivery mode for metadata-only workflows.
- Stream management helpers for retention, discard, storage, replicas, and
  duplicate-window documentation.
- Stream mirror, source, subject transform, republish, compression, placement,
  and metadata management helpers beyond the current documentation guidance.
- Optional no-echo connection setting.
- Sink certification tests for future HTTP, S3, Kafka, and other active sink
  proposals.

## Not Planned Unless Scope Changes

- `AckNone` and early-ACK behavior, because they conflict with
  commit-then-acknowledge.
- `AckAll`, because implicit batch ACK behavior can obscure per-message durable
  success.
- `AckNext`, because ACK-plus-fetch behavior would couple acknowledgement,
  backpressure, and pull timing in a way that is harder to audit than the
  current explicit fetch loop.
- General-purpose Core NATS pub/sub framework behavior.
- Core NATS queue groups as a sink-scaling mechanism; pull consumers remain the
  preferred model.
- Request/reply and NATS services framework support.
- JetStream Key/Value and Object Store APIs, unless a future sink or operational
  workflow requires them.
- Postgres sink implementation. This can be reconsidered if a maintainer,
  contributor, customer deployment, certification need, or funding source
  changes the scope and commits to the required production-quality sink work.
