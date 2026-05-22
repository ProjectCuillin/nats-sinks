# Operations

This page describes deployment and runtime behavior for operators. It assumes
you want to run `nats-sinks` as a long-lived process that continuously moves
messages from JetStream into a destination system.

Operationally, the most important idea is that the process is allowed to see
duplicate messages. That is part of at-least-once delivery. The unsafe outcome
is not duplication; the unsafe outcome is ACKing a message before the
destination write has committed.

For mission-oriented and defence workloads, think of `nats-sinks` as a small
ingestion component in a larger operational picture. It may sit near
sensor-fusion, command-and-control, sensor-to-shooter, kill-chain, kill-mesh,
or weapon-system status reporting workflows, but its job is not to select
targets, authorize effects, control weapons, or decide mission policy. Its job
is to preserve the event trail, make failures visible, and avoid converting a
temporary downstream problem into silent data loss.

The [Defence And Mission Support](use-cases/defence/index.md) blueprint pages
show how current generic features can be combined for sensor event custody,
classification and labels, chain-of-custody evidence, cross-domain handoff
preparation, edge operation, and audit-oriented persistence without changing
the product into a defence-only platform.

## Deployment Shape

`nats-sinks` can run as:

- a systemd service,
- a container,
- a Kubernetes Deployment,
- a process managed by another Python application.

Typical command:

```bash
nats-sink run /etc/nats-sinks/config.json
```

Kubernetes deployment examples are provided in
[Kubernetes Deployment](kubernetes.md). They show JSON runtime configuration in
ConfigMaps, Secret references for credentials, restrictive security contexts,
resource requests and limits, readiness and liveness checks, graceful
termination settings, and optional Prometheus observability sidecars. The
examples are public-safe starting points; operators must replace all
placeholders with environment-specific values before applying them.

Before starting a production worker, confirm that its NATS account has only the
runtime permissions it needs. In the preferred model, stream and durable
consumer creation are handled by a separate administrative process. The worker
account can fetch from one consumer, receive inbox responses, ACK received
messages after durable sink success, and publish to the configured DLQ subject
only when DLQ is enabled. Templates are provided in
[NATS Least-Privilege Permissions](nats-permissions.md).

If the selected stream is built from mirrors, sources, subject transforms,
republish rules, compression, placement policies, or stream metadata, review
the topology before treating the sink's idempotency key as final. Those
features are managed outside `nats-sinks`, but they can change the subject,
stream sequence, replay path, and operator context that the worker observes.
See [Advanced JetStream Topology](jetstream-topology.md).

## Runtime Lifecycle

```mermaid
stateDiagram-v2
    [*] --> LoadConfig
    LoadConfig --> StartSink
    StartSink --> ConnectNATS
    ConnectNATS --> FetchBatch
    FetchBatch --> WriteBatch
    WriteBatch --> AckBatch: sink success
    WriteBatch --> TemporaryFailure: temporary error
    WriteBatch --> DLQ: permanent error
    TemporaryFailure --> FetchBatch
    DLQ --> AckBatch: DLQ publish success
    AckBatch --> FetchBatch
    FetchBatch --> Shutdown: stop requested
    Shutdown --> StopSink
    StopSink --> [*]
```

## Logging

The package uses standard Python logging. Payload logging is disabled by
default because message bodies may contain business data, customer data, or
encrypted payloads. Avoid DEBUG logs in production unless you have reviewed
payload and credential exposure risks.

Use `INFO` for ordinary service operation, `WARNING` when you want only
recoverable problems and risky conditions, and `ERROR` or `CRITICAL` when the
runtime should report only serious failures. Use `DEBUG` for short-lived
diagnostic sessions in controlled environments.

In watch-floor, operations-center, or mission-support deployments, keep logs
boring and actionable. Prefer stable event counts, DLQ alerts, and last-success
timestamps over verbose payload logs. Payloads and headers may carry sensitive
operational context even when they look harmless during a test.

The full logging level reference is documented in
[Configuration](configuration.md#logging).

## Metrics

`nats-sinks` exposes a small metrics abstraction that can be connected to an
embedding application's Prometheus, OpenTelemetry, StatsD, or platform-native
telemetry stack. The command-line process uses a no-op recorder by default, but
it can write a local JSON metrics snapshot when `metrics.enabled` is true and
`metrics.snapshot_file` is configured. The separate `nats-sink-metrics` CLI can
then inspect that snapshot without connecting to NATS or a destination backend.

```json
{
  "metrics": {
    "enabled": true,
    "namespace": "nats_sinks",
    "snapshot_file": ".local/nats-sinks/metrics.json"
  }
}
```

```bash
nats-sink-metrics show .local/nats-sinks/metrics.json --format table
nats-sink-metrics get .local/nats-sinks/metrics.json messages_failed_total --default 0
```

The full CLI reference, Python hooks, shell examples, exit codes, and snapshot
schema are documented in [Metrics](metrics.md).

Confirmed JetStream acknowledgement support has been evaluated for a future
release, but it is not enabled today. If that option is implemented later, it
will provide stronger evidence that the server accepted the post-commit ACK,
while still allowing redelivery if confirmation fails after durable sink
success. See
[Acknowledgement Confirmation Evaluation](acknowledgement-confirmation.md).

JetStream `InProgress` support has also been evaluated for future long-running
sink writes. It is not enabled today. If implemented, it should be treated as a
bounded heartbeat around active work, not as a success signal. See
[InProgress Evaluation](in-progress-evaluation.md).

Ordered-consumer support has been evaluated for future inspection and analysis
tooling. It is not enabled today and should not be used as a replacement for
durable pull-consumer sink workers. Replay into sinks should use durable
pull-consumer semantics with commit-then-acknowledge. See
[Ordered Consumer Evaluation](ordered-consumer-evaluation.md).

Push-consumer support has also been evaluated, but it is not enabled today.
Future push mode must be explicit, manual-ACK only, bounded by queue and
pending-byte limits, and tested for graceful shutdown before it can be treated
as production-ready. Pull consumers remain the operational default because the
worker controls when and how many messages are fetched. See
[Push Consumer Evaluation](push-consumer-evaluation.md).

Prometheus sharing should use the policy-controlled observability layer rather
than an ad hoc shell redirection. The separate `nats-sink-observe` CLI can
generate a disabled policy from runtime config, write a filtered textfile for
node_exporter, or run an optional native HTTP endpoint:

```bash
nats-sink-observe init-prometheus-policy \
  /etc/nats-sinks/config.json \
  /etc/nats-sinks/observability.prometheus.json

nats-sink-observe prometheus-textfile \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json \
  --output /var/lib/node_exporter/textfile_collector/nats_sinks.prom

nats-sink-observe prometheus-http \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json \
  --dry-run
```

The observability service should run separately from the sink worker where
possible. See [Observability](observability.md) for the sharing model, the
[Prometheus Integration](prometheus.md) observability sub-page for connector
details, and [Running nats-sink As A Service](service-deployment.md) for the
service model.

NATS server monitoring endpoints such as `/jsz` and `/healthz` should be
monitored through your NATS or platform monitoring stack, or through the
separate disabled-by-default `nats-sink-observe nats-monitoring-poll`
connector when selected fields must pass through the `nats-sinks`
observability policy. The delivery-critical sink worker never polls these
endpoints. `nats-sinks` documents this boundary in
[NATS Server Monitoring Integration](nats-server-monitoring.md): server
monitoring is useful operational context, but it must not change ACK, retry,
DLQ, or sink write behavior.

Metric names are emitted to recorders as suffixes. Exporters should prefix them
with the configured namespace, which defaults to `nats_sinks`. For example,
the emitted suffix `messages_fetched_total` is conventionally exported as
`nats_sinks_messages_fetched_total`.

The preferred basic metric set is:

| Metric suffix | Type | Meaning |
| --- | --- | --- |
| `messages_fetched_total` | counter | Raw JetStream messages accepted by the runner for processing. |
| `messages_prepared_total` | counter | Messages converted into `NatsEnvelope` objects and transformed by core policies such as payload encryption and metadata resolution. |
| `messages_written_total` | counter | Messages for which `sink.write_batch(...)` returned durable success. |
| `messages_acked_total` | counter | Messages ACKed to JetStream after durable sink success or successful DLQ publication. |
| `messages_terminated_total` | counter | Messages terminally acknowledged to JetStream after successful DLQ publication when the opt-in DLQ policy is enabled. |
| `messages_nacked_total` | counter | Messages negatively acknowledged after retryable failure paths when `temporary_failure_action` is `nak`. |
| `messages_failed_total` | counter | Messages that entered a failure path before ACK. |
| `messages_dlq_total` | counter | Messages successfully published to a configured dead-letter subject. |
| `batches_fetched_total` | counter | Non-empty batches processed by the runner. |
| `nats_fetch_seconds` | histogram/observation | Elapsed time spent waiting for JetStream pull fetch calls. |
| `message_mapping_seconds` | histogram/observation | Elapsed time spent converting raw NATS messages into internal envelopes. |
| `sink_batches_written_total` | counter | Batches for which the sink returned durable success. |
| `sink_batch_write_seconds` | histogram/observation | Elapsed time spent inside `sink.write_batch(...)` for successful batches. |
| `oracle_execute_seconds` | histogram/observation | Elapsed time spent executing Oracle batch write statements before commit. |
| `oracle_commit_seconds` | histogram/observation | Elapsed time spent committing Oracle transactions. |
| `message_ack_seconds` | histogram/observation | Elapsed time spent ACKing JetStream messages after durable success. |
| `message_term_seconds` | histogram/observation | Elapsed time spent sending terminal acknowledgements after successful DLQ publication. |
| `retry_backoff_delay_seconds` | histogram/observation | Retry delay seconds selected before delayed NAK on retryable failures. |
| `sink_write_errors_total` | counter | Sink write failures raised before durable success. |
| `message_normalization_errors_total` | counter | Raw NATS messages that could not be normalized into envelopes. |
| `payload_encryption_errors_total` | counter | Messages that failed core payload encryption before sink delivery. |
| `dlq_publish_errors_total` | counter | Messages whose DLQ publication failed, leaving the original message unacked. |
| `ack_errors_total` | counter | Messages whose JetStream ACK failed after durable success. |
| `term_errors_total` | counter | Messages whose JetStream terminal acknowledgement failed after successful DLQ publication. |
| `nats_connection_disconnected_total` | counter | NATS client disconnect events observed by the runner. |
| `nats_connection_reconnected_total` | counter | NATS client reconnect events observed by the runner. |
| `nats_connection_closed_total` | counter | NATS client closed events observed by the runner. |
| `nats_discovered_servers_total` | counter | NATS discovered-server events observed by the runner. |
| `nats_async_errors_total` | counter | NATS asynchronous error callback events observed by the runner. |
| `last_sink_success_epoch_seconds` | gauge | Unix epoch seconds for the latest durable sink success followed by ACK. |
| `current_batch_messages` | gauge | Number of messages in the current active batch. |

For compatibility with earlier local dashboards and test tooling, the runner
also emits legacy aliases for a few original names:

| Legacy suffix | Preferred suffix |
| --- | --- |
| `messages_received_total` | `messages_prepared_total` |
| `batches_written_total` | `sink_batches_written_total` |
| `batch_write_seconds` | `sink_batch_write_seconds` |
| `last_success_timestamp` | `last_sink_success_epoch_seconds` |
| `current_batch_size` | `current_batch_messages` |

Avoid high-cardinality labels in exporters. Good labels are stable operational
dimensions such as sink type, stream, consumer, result, or deployment
environment. Avoid labels such as message ID, stream sequence, raw subject,
classification, route name, or application labels unless you have a deliberate
cardinality and sensitivity policy.

Useful alerting signals include:

- `messages_failed_total` increasing faster than `messages_written_total`,
- `sink_write_errors_total` rising after a deployment or database change,
- `dlq_publish_errors_total` being greater than zero,
- `ack_errors_total` being greater than zero,
- `nats_connection_disconnected_total` rising without a corresponding
  `nats_connection_reconnected_total`,
- `nats_async_errors_total` increasing during normal traffic,
- `last_sink_success_epoch_seconds` becoming stale for an active stream,
- `current_batch_messages` staying high while write throughput drops.

Metrics must never affect delivery semantics. A recorder failure should be
handled by the embedding exporter; the core runtime still follows
commit-then-acknowledge and must not ACK early just because telemetry failed.

Backend write timing is emitted through `sink_batch_write_seconds`. This is a
functional measurement around `sink.write_batch(...)`, including the sink's
durable commit or durable completion work. It is useful for spotting regressions
and comparing local test runs, but a single e2e timing line is not a production
benchmark. Treat throughput numbers from local or lab tests as environment
observations until you have a documented benchmark plan, realistic payloads,
repeatable infrastructure, and clear p95/p99 latency goals.

For Oracle deployments using high-throughput staging mode, monitor the staging
table as an operational object, not as an implementation detail. The default
`delete_on_success` cleanup removes staged rows before commit; retained rows may
indicate a failed transaction, an intentionally configured `cleanup="keep"`
review mode, or an external cleanup gap. Alert on unexpected staging-table
growth and document who may inspect or purge staged operational data.

## Load And Failure Rehearsals

Operators and maintainers can run synthetic load profiles before a live
benchmark or deployment change. The profiles do not connect to NATS, Oracle, a
file sink, Prometheus, or any private service. They generate local fake
messages and exercise the framework's normal, retry, DLQ, and shutdown
reporting paths with sanitized output.

```bash
python scripts/run-load-profile.py --profile normal --message-count 256 --batch-size 64
python scripts/run-load-profile.py --profile retry --message-count 256 --batch-size 64
python scripts/run-load-profile.py --profile dlq --message-count 256 --batch-size 64
python scripts/run-load-profile.py --profile shutdown --message-count 250 --batch-size 64
```

Use these profiles as operational rehearsals and regression indicators, not as
formal capacity claims. A production-like performance run still needs realistic
payload sizes, real destination service classes, representative NATS topology,
and a documented security review. The profile reference is in
[Performance](performance.md#synthetic-load-profiles); the test workflow is in
[Testing](testing.md#synthetic-load-profile-tests).

## Retry Backoff

Retryable failures should slow down under pressure instead of creating a tight
redelivery loop. The core runner therefore supports fixed, linear, and
exponential delayed NAK backoff with optional jitter. The retry delay is based
on JetStream delivery-attempt metadata when available and is capped by
`delivery.retry_backoff_max_ms`.

The default mode is exponential backoff with full jitter. This is a conservative
operational default for shared outages: if several sink instances lose access
to the same database, filesystem, or network segment, jitter reduces the chance
that all instances retry at exactly the same moment.

Exhausting `delivery.max_retries` does not ACK the message. The runner simply
stops issuing active delayed NAKs for that failure and leaves the message
redeliverable for JetStream consumer policy, including externally configured
`AckWait`, `MaxDeliver`, advisories, and operational intervention. This keeps
the system aligned with the project rule: commit first, ACK last, design for
redelivery.

## Priority Lanes

Priority-aware processing lanes can be enabled when mixed-urgency events are
fetched into the same bounded batch. The runner assigns each message to a lane
from normalized `priority` metadata, then uses weighted round-robin to order the
current batch before sink delivery.

Use this as a local backlog-drain control, not as a global ordering guarantee.
JetStream still controls delivery to the pull consumer, and nats-sinks still
ACKs only after durable sink success. Missing or unknown priority values should
normally fall back to a default lane; deployments with a strict metadata
contract may configure unknown values to fail closed and rely on DLQ handling.

Read [Priority-Aware Processing Lanes](priority-lanes.md) for configuration,
metrics, starvation controls, and security guidance.

## Payload Encryption Key Rotation

Payload encryption key rotation is an operational runbook, not a background
task hidden inside the runner. The runner encrypts new messages with the active
configured key. Existing destination records keep the `key_id` that was written
into their encrypted payload envelope at the time of storage.

A safe rotation window normally looks like this:

1. Generate new AES-256 key material through the approved key-management
   process.
2. Store it in the platform secret manager or protected service environment.
3. Deploy nats-sinks configuration with a new non-secret `key_id`.
4. Keep the previous key available to authorized replay, migration, audit, or
   incident-response tooling.
5. Retire the previous key only after all records encrypted with it are outside
   the required retention and replay window.

Use `PayloadKeyRegistry` in authorized tooling when records may have been
written by more than one key generation. The registry reads the stored `key_id`
and selects the matching decryptor. Unknown key identifiers fail closed rather
than falling back to another key.

## Graceful Shutdown

The runner should stop fetching new messages before shutdown and let the active
batch reach a durable boundary. If the process exits before ACK, JetStream may
redeliver. Idempotency is required, and production sinks should treat
redelivery as a normal operational event rather than an exceptional condition.

## Reprocessing

Do not claim exactly-once processing. Replays and duplicates are normal. Use idempotent sink modes and stable keys before replaying streams.

Before replaying operational streams, confirm the destination idempotency mode,
retention expectations, and any audit implications. A replay should be a
controlled recovery action, not an accidental duplicate-production event.

For complete operational patterns that combine retry, DLQ, file handoff, and
restricted storage guidance, see
[Mission-Support Operational Examples](use-cases/mission-support/index.md).

## Local File Sink Operations

The file sink is operationally simple, but it still needs capacity planning.
Monitor disk space, inode usage, write latency, and backup or rotation jobs for
the configured output directory. The recommended production configuration uses
`filename_strategy: "stream_sequence"` and `duplicate_policy:
"skip_existing"` so redelivery maps to the same final file and is treated as
safe prior durable success.

Use an absolute directory path in service deployments. Keep generated files out
of git and out of world-writable directories. If host-crash durability matters,
leave `fsync` enabled and size throughput expectations accordingly.

Optional gzip compression can reduce disk usage for JSON and text-heavy
streams, but it is not a retention or privacy control. Compressed files still
need the same access controls, backup policy, and rotation policy as
uncompressed files.

## Docker Compose Examples

The examples directory includes JSON-formatted Compose files:

```bash
docker compose -f examples/docker-compose.nats.json up
docker compose -f examples/docker-compose.oracle.json up
```

## systemd Services

For Oracle Linux and Debian systemd examples, see
[Service Deployment](service-deployment.md).
