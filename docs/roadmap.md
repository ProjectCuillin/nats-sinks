# Roadmap

## Phase 1

- Core runtime.
- Oracle sink.
- File sink.
- Oracle idempotency support with `stream_sequence`, `message_id`, and
  `payload_field` strategies.
- Oracle duplicate-safe production write modes: `merge` and `insert_ignore`.
- File sink idempotency support with deterministic file names and
  `skip_existing` duplicate handling.
- Core payload encryption with AES-256-GCM and AES-256-CCM before sink writes,
  including global and per-subject encryption policies.
- Fail-closed pre-sink policy enforcement for subject-scoped requirements such
  as priority, classification, labels, mission metadata, encrypted payloads,
  approved mission metadata keys, and bounded sink-bound payload size.
- Core size policy enforcement for sink-bound payload bytes, headers, labels,
  mission metadata, standard metadata, approximate record size, and accepted
  batch size before any sink write.
- Core message metadata for priority, classification, and labels so
  mission-oriented deployments can preserve operational handling context across
  all production sinks.
- Optional message authenticity verification before sink writes, with
  subject-scoped HMAC-SHA256 and Ed25519 rules, sanitized rejection reasons,
  aggregate metrics, and DLQ-before-ACK behavior for failed signatures.
- Generic mission metadata support for one validated JSON context object across
  the core runtime, Oracle `MISSION_METADATA_JSON`, file-sink output, and future
  sink contracts.
- Optional tamper-evident custody metadata with deterministic payload,
  metadata, and record hashes computed by the core before sink writes and
  persisted by Oracle, file, and future sinks.
- Basic metrics counters and observations for fetched, prepared, written,
  ACKed, NAKed, failed, DLQ, sink write, ACK error, and active batch behavior.
- Event freshness and staleness metrics for aggregate event age at receive and
  store time, missing or malformed creation timestamps, stale events, and
  positive source clock skew.
- Stable InProgress metric names, metrics CLI rendering, Prometheus text
  rendering, and an operator runbook for distinguishing slow active work from
  durable sink success.
- Optional disabled-by-default JetStream `InProgress` heartbeat during
  long-running sink writes, with effective AckWait-only startup guardrails,
  bind-only consumer-policy inspection, BackOff rejection, and bounded
  interval, count, and shutdown controls.
- Local JSON metrics snapshots and the `nats-sink-metrics` inspection CLI for
  table, JSON, JSONL, shell, names, and Prometheus text output.
- Observability core with disabled-by-default sharing policies and a
  `nats-sink-observe` CLI for safe connector operation.
- Subject-aware observability policy model and bounded subject-family metric
  aggregation through prepared `labeled_metrics` snapshot rows, with raw
  subject export disabled by default.
- Subject-aware observability certification tests and operator runbook proving
  disabled defaults, sanitized connector output, cardinality controls, and
  delivery non-interference for prepared subject-family metrics.
- Policy-controlled Prometheus textfile connector for node_exporter, designed
  to run as a separate Linux service from the sink worker.
- Optional native Prometheus HTTP scrape endpoint, designed as a separate
  disabled-by-default observability service that reads policy-filtered local
  metrics snapshots.
- OpenTelemetry OTLP metrics connector for deployments using collectors,
  implemented as a disabled-by-default observability command that reads local
  metrics snapshots and shares only policy-approved metric names.
- Grafana Alloy observability profile over the shared OTLP connector,
  including generated Alloy River snippets and disabled-by-default export.
- Splunk HEC observability connector for approved aggregate metrics in
  security operations and incident-response environments, with token values
  sourced from environment variables and HEC export kept outside delivery.
- OCI Monitoring observability connector for approved Oracle Cloud
  Infrastructure custom metrics, with optional OCI SDK dependency,
  least-privilege identity guidance, and export kept outside delivery.
- StatsD observability connector for approved best-effort UDP or Unix datagram
  metric export, kept outside delivery semantics.
- Amazon CloudWatch observability connector for approved custom metrics through
  bounded `PutMetricData` requests and the optional AWS SDK path.
- Syslog observability bridge for approved bounded RFC 5424-style metric
  messages over UDP or Unix datagram sockets, kept outside delivery semantics.
- NATS server monitoring diagnostic connector for selected endpoints such as
  `/jsz` and `/healthz`, implemented outside the delivery worker with explicit
  endpoint and field allow lists.
- Kubernetes deployment examples with JSON ConfigMaps, Secret references,
  mounted trust material, security contexts, resource limits, graceful
  shutdown settings, and optional Prometheus observability sidecars.
- Oracle duplicate/conflict metrics and configurable Oracle `merge`
  update-column controls for idempotent Oracle operations, readable through
  the same metrics snapshot and `nats-sink-metrics` CLI.
- Oracle subject-to-table routes with optional per-route idempotency overrides
  for stream sequence, message ID, and payload-field keys.
- Read-only Oracle lineage query helpers for allow-listed mission metadata,
  message ID, and subject lookups, with bounded result limits, parameterized
  values, redacted output, and no effect on sink writes or ACK behavior.
- Multiple NATS seed URLs, reconnect tuning, and connection event metrics for
  clustered or controlled-network deployments.
- WebSocket connection guardrails for explicit `ws://` and `wss://` transport,
  mixed URL-list rejection, credential-free URLs, `wss://` local CA behavior,
  optional redacted WebSocket connection headers, and a local certification
  harness.
- Optional NATS no-echo connection setting for specialized same-connection
  publish/subscribe policy requirements.
- Safe sink connector framework with `SinkConnector` metadata, explicit
  `SinkRegistry` resolution, first-party Oracle and FileSink descriptors, and
  disabled-by-default allow-listed entry-point discovery for reviewed external
  connectors.
- Documented sink certification contract with reusable test helpers for
  lifecycle, durable write success, duplicate redelivery, ACK-boundary
  protection, and log-redaction checks across current and future production
  sinks.
- Optional JetStream advisory observation for selected `$JS.EVENT.ADVISORY...`
  subjects with disabled-by-default configuration and aggregate metrics only.
- Explicit durable pull-consumer management with `bind_only`,
  `create_if_missing`, and `reconcile` modes, plus safe drift validation for
  delivery-sensitive settings such as filter subject, ACK policy, AckWait,
  MaxDeliver, MaxAckPending, and headers-only state.
- Opt-in bounded manual-ACK push-consumer mode with fail-closed configuration,
  guarded callback intake, flow-control and idle-heartbeat propagation, and
  delivery-contract certification tests.
- Encrypted edge spool-and-forward sink for disconnected operation, with
  bounded local custody, deterministic duplicate handling, priority-aware
  replay, and explicit forwarding into a final destination sink.
- Richer durable pull-consumer policy configuration for plural filter subjects,
  server-side BackOff, MaxWaiting, consumer replicas, memory-storage state, and
  bounded low-sensitivity consumer metadata.
- Durable replay-to-sinks guidance and tooling design based on durable pull
  consumers, explicit replay boundaries, dry-run evidence, redacted reporting,
  idempotency review, and no-early-ACK test expectations.
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
- Cross-domain handoff package blueprint documentation with a manifest schema,
  sanitized example package, path-safety constraints, hash validation, and clear
  non-goals stating that `nats-sinks` is not a cross-domain guard or
  certification boundary.
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

- Individual observability connector backlog items for Datadog, Amazon
  CloudWatch, and Azure Monitor, all following the shared disabled-by-default
  observability connector contract.
- Individual observability connector backlog items for Datadog, Oracle Cloud
  Infrastructure Monitoring, and Azure Monitor, all
  following the shared disabled-by-default observability connector contract.
- Headers-only JetStream delivery support split into validated consumer
  configuration, payload-presence metadata, and sink or DLQ certification.
- Additional mission-support documentation examples for future operator
  runbooks, deeper replay drills, and sink-specific certification evidence.
- Deeper certification evidence and runbooks for complex multi-route Oracle
  idempotency deployments.
- Deeper Oracle merge insert-versus-match visibility if future Oracle driver
  metadata can support it reliably without guessing.
- HTTP sink idempotency-key support, retry safety guidance, and clear warnings
  for endpoints that cannot provide idempotent semantics.
- S3 sink design with atomic object keys and safe duplicate overwrite/skip
  behavior.
- Native Oracle Cloud Infrastructure Object Storage sink design with
  deterministic object keys, OCI identity support, checksums, multipart upload,
  and least-privilege bucket guidance.
- Additional Oracle MySQL HeatWave tuning and certification guidance on top of
  the implemented first-party Oracle MySQL sink, including deployment profiles,
  performance notes, and HeatWave-specific operational validation.
- Oracle Berkeley DB, Oracle NoSQL Database, Oracle Coherence Community
  Edition, and OCI Streaming sink evaluations as first-party Oracle-family
  connector candidates. A local Oracle Coherence Community Edition test backend
  is now available as development infrastructure for the future sink and
  routing tests, but the sink itself remains backlog work.
- Additional Oracle-family connector evaluations such as Oracle Autonomous AI
  Lakehouse, Oracle AI Data Platform, Oracle JSON document stores, OCI Cache
  Cluster, WebLogic JMS, Oracle TimesTen, Oracle Spatial and Graph profiles,
  Oracle application connector families, and OCI PostgreSQL profile decisions.
- Live-certification work for the experimental Palantir Foundry Streams sink
  and experimental Palantir Gotham RevDB object sink. Both connectors now have
  local fake-client contract harnesses, but no production certification claim.
- Elasticsearch or OpenSearch, Snowflake, BigQuery, Azure object storage,
  Kafka, MongoDB, Redis, and Cassandra-compatible sink evaluations at low
  priority so the project can learn from common Kafka-style connector patterns
  without prematurely broadening the production surface.
- Additional external connector evaluations, including AWS streaming and
  warehouse targets, Azure Event Hubs and Microsoft Fabric, Google Cloud
  Storage and Pub/Sub, Databricks, Apache Iceberg and Hadoop ecosystem targets,
  JMS, JDBC, SQL Server, Db2, SAP HANA, specialty warehouses, distributed SQL
  systems, legacy database families, Solace, managed Kafka compatibility
  profiles, Cosmos DB profiles, and MariaDB.
- HTTP sink.
- Docker image.
- Optional dedicated secret-manager connectors for encryption keys when a
  future release can keep provider dependencies isolated behind extras.
- Expanded property-based or dedicated fuzz tooling if the deterministic
  bounded generator suite reaches its limits or future parsers become more
  complex.
- Expanded mission metadata schema policy controls for deployments that need
  stricter validation than the current root-key allow list and size policy.
- Hash-verified installation guidance for high-trust environments.
- Expanded live certification runbooks for NATS TLS certificate, NKEY,
  credentials-file, and decentralized JWT deployments across representative
  server policies.
- Deeper replay-start options for sequence or timestamp-based delivery
  policies.
- Optional confirmed ACK support after durable sink success.
- Optional confirmed ACK or terminal acknowledgement handling after successful
  DLQ publication.
- ACK confirmation metrics and an operator runbook for interpreting durable
  success followed by ACK confirmation failure.
- Explicit BackOff-aware `InProgress` heartbeat timing if future work can prove
  safe support for JetStream BackOff sequences.

## Phase 3

- External connector marketplace guidance, certification evidence, and
  governance beyond the current allow-listed entry-point framework.
- Sink certification tests.
- Helm chart.
- Advanced observability.
- Payload-presence metadata and sink certification for headers-only
  metadata-only workflows.
- Stream management helpers for retention, discard, storage, replicas, and
  duplicate-window documentation.
- Stream mirror, source, subject transform, republish, compression, placement,
  and metadata management helpers beyond the current documentation guidance.
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

See [NATS Feature Gap Analysis](nats-feature-gap-analysis.md) for the detailed
comparison between NATS platform capabilities and current `nats-sinks` scope.
