# Operations

This page describes deployment and runtime behavior for operators. It assumes
you want to run `nats-sinks` as a long-lived process that continuously moves
messages from JetStream into a destination system.

Operationally, the most important idea is that the process is allowed to see
duplicate messages. That is part of at-least-once delivery. The unsafe outcome
is not duplication; the unsafe outcome is ACKing a message before the
destination write has committed.

For mission-oriented and defence workloads, think of `nats-sinks` as a small
ingestion component in a larger operational picture. Its job is not to decide
mission policy; its job is to preserve the event trail, make failures visible,
and avoid converting a temporary downstream problem into silent data loss.

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

The first release includes a metrics abstraction. Intended counters include:

- `messages_received_total`
- `messages_written_total`
- `messages_acked_total`
- `messages_nacked_total`
- `messages_failed_total`
- `messages_dlq_total`
- `batches_written_total`
- `batch_write_seconds`
- `sink_write_errors_total`
- `nats_reconnects_total`
- `last_success_timestamp`
- `current_batch_size`

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
