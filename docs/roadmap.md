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
- Commit-then-acknowledge contract tests proving ACK happens only after sink
  success and DLQ publication succeeds before ACK.
- CLI.
- Documentation.
- Tests.
- PyPI-ready package.

## Phase 2

- Better metrics.
- Documented sink certification contract for idempotency, including required
  duplicate-redelivery tests for every new production sink.
- Per-route or per-table Oracle idempotency overrides for deployments where
  different subjects need different durable keys.
- More Oracle duplicate handling controls, such as optional conflict counters,
  duplicate metrics, and clearer `merge` update-column controls.
- Oracle high-throughput write mode using array-loaded staging tables followed
  by one set-based merge into the destination table.
- Oracle benchmark scripts that report publish, fetch, map, backend write,
  commit, and ACK timing separately.
- Postgres sink with `ON CONFLICT`-based idempotent `merge` and `insert_ignore`
  behavior.
- HTTP sink idempotency-key support, retry safety guidance, and clear warnings
  for endpoints that cannot provide idempotent semantics.
- S3 sink design with atomic object keys and safe duplicate overwrite/skip
  behavior.
- Postgres sink.
- HTTP sink.
- Docker image.
- Kubernetes examples.
- Multiple NATS seed URLs for clustered deployments.
- NATS reconnect tuning and connection event metrics.
- Least-privilege NATS permissions templates for sink users.
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
- Prometheus or OpenTelemetry metrics export.

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
- Stream mirror, source, subject transform, and republish documentation.
- Stream compression, placement, and metadata guidance.
- Server monitoring endpoint integration such as `/jsz`.
- `AckTerm` and `AckNext` evaluation for advanced failure and fetch workflows.
- Optional no-echo connection setting.
- Sink certification tests for future Postgres, HTTP, S3, and Kafka sinks.

## Not Planned Unless Scope Changes

- `AckNone` and early-ACK behavior, because they conflict with
  commit-then-acknowledge.
- `AckAll`, because implicit batch ACK behavior can obscure per-message durable
  success.
- General-purpose Core NATS pub/sub framework behavior.
- Core NATS queue groups as a sink-scaling mechanism; pull consumers remain the
  preferred model.
- Request/reply and NATS services framework support.
- JetStream Key/Value and Object Store APIs, unless a future sink or operational
  workflow requires them.

See [NATS Feature Gap Analysis](nats-feature-gap-analysis.md) for the detailed
comparison between NATS platform capabilities and current `nats-sinks` scope.
