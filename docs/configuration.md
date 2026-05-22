# Configuration

Runtime configuration is JSON-only. `nats-sinks` reads UTF-8 JSON files, requires a JSON object at the root, applies a small explicit allow-list of environment overrides, and validates the final structure with Pydantic.

The configuration model is deliberately explicit. In operational, defence, or
public-sector deployments, configuration is often reviewed by more than one
team. JSON files, strict validation, redacted effective output, and named
environment-variable secrets make it easier to review what a sink service will
connect to, what it will consume, which metadata defaults it will apply, and
where it will write.

## Minimal Configuration

The minimal example uses the local file sink because it does not require a
database or credentials. Oracle uses the same generic runtime sections and adds
Oracle-specific fields inside the `sink` object.

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "sink": {
    "type": "file",
    "directory": ".local/file-sink/events",
    "filename_strategy": "stream_sequence",
    "duplicate_policy": "skip_existing"
  }
}
```

## Full Example

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "urls": [],
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*",
    "durable": true,
    "token_env": "NATS_TOKEN",
    "tls_ca_file": "/etc/nats/certs/ca.crt",
    "tls_verify": true,
    "allow_reconnect": true,
    "connect_timeout_seconds": 2,
    "reconnect_time_wait_seconds": 2,
    "max_reconnect_attempts": 60,
    "ping_interval_seconds": 120,
    "max_outstanding_pings": 2,
    "pending_size_bytes": 2097152,
    "drain_timeout_seconds": 30
  },
  "delivery": {
    "batch_size": 100,
    "batch_timeout_ms": 1000,
    "max_in_flight_batches": 1,
    "ack_policy": "after_sink_commit",
    "max_retries": 5,
    "retry_backoff_ms": 1000,
    "retry_backoff_max_ms": 60000,
    "retry_backoff_mode": "exponential",
    "retry_backoff_multiplier": 2.0,
    "retry_jitter": "full",
    "temporary_failure_action": "nak",
    "prefer_safe_duplication": true
  },
  "dead_letter": {
    "enabled": true,
    "subject": "orders.dlq",
    "include_payload": true,
    "include_headers": true,
    "include_error": true,
    "ack_term_after_publish": false
  },
  "logging": {
    "level": "INFO",
    "payload_logging": false
  },
  "metrics": {
    "enabled": false,
    "namespace": "nats_sinks",
    "snapshot_file": null
  },
  "advisories": {
    "enabled": false,
    "subjects": [
      "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",
      "$JS.EVENT.ADVISORY.CONSUMER.MSG_NAKED.*.*",
      "$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.*.*"
    ],
    "max_payload_bytes": 65536,
    "log_events": false
  },
  "message_metadata": {
    "priority": {
      "header": "Nats-Sinks-Priority",
      "default": "normal"
    },
    "classification": {
      "header": "Nats-Sinks-Classification",
      "default": null
    },
    "labels": {
      "header": "Nats-Sinks-Labels",
      "default": []
    },
    "rules": [
      {
        "subject": "orders.urgent.>",
        "priority": "urgent",
        "classification": "restricted",
        "labels": "urgent;customer-facing"
      }
    ]
  },
  "encryption": {
    "enabled": false,
    "algorithm": "aes-256-gcm",
    "key_id": "orders-runtime-key",
    "key_b64_env": "NATS_SINKS_PAYLOAD_KEY_B64",
    "nonce_size_bytes": 12,
    "tag_length": 16,
    "rules": [
      {
        "subject": "secure.>",
        "enabled": true,
        "key_id": "secure-runtime-key",
        "key_b64_env": "NATS_SINKS_SECURE_PAYLOAD_KEY_B64"
      }
    ]
  },
  "custody": {
    "enabled": false,
    "algorithm": "sha256",
    "hash_payload": true,
    "hash_metadata": true,
    "include_previous_hash": false,
    "previous_hash_header": "Nats-Sinks-Previous-Custody-Hash",
    "key_id": null,
    "max_hash_input_bytes": 16777216
  },
  "pre_sink_policy": {
    "enabled": false,
    "unmatched_subject_action": "reject",
    "rules": [
      {
        "subject": "orders.secure.>",
        "require_priority": true,
        "require_classification": true,
        "required_labels": ["orders"],
        "require_mission_metadata": true,
        "require_encrypted_payload": true,
        "max_payload_bytes": 1048576,
        "allowed_mission_metadata_keys": ["profile", "phase", "operation"]
      }
    ]
  },
  "sink": {
    "type": "file",
    "directory": ".local/file-sink/events",
    "mode": "one_file_per_message",
    "filename_strategy": "stream_sequence",
    "duplicate_policy": "skip_existing",
    "payload_mode": "json_or_envelope",
    "compression": "none",
    "include_metadata": true,
    "partition_by_subject": true,
    "create_directory": true,
    "fsync": true
  }
}
```

## Configuration File Rules

Configuration files are normal JSON documents. The root value must be an
object, comments are not allowed, duplicate object keys are rejected,
Python-specific constants such as `NaN` and `Infinity` are rejected, and
unknown fields in the generic runtime sections are rejected. Configuration
files are also bounded to 1 MiB. This strictness is intentional: production
sink services should fail early when an operator misspells a field, places an
option in the wrong section, accidentally carries configuration from another
deployment, or provides ambiguous JSON that different tools might interpret
differently.

The top-level sections are:

| Section | Required | Purpose |
| --- | --- | --- |
| `nats` | yes | NATS server connection, JetStream stream, consumer, subject, authentication, and TLS settings. |
| `delivery` | no | Batching, ACK policy, retry, and temporary failure behavior. Defaults are safe for local and early production deployments. |
| `dead_letter` | no | Optional DLQ publication for permanently invalid messages. |
| `logging` | no | Standard Python logging level and payload logging switch. |
| `metrics` | no | Metrics namespace, enablement flag, and optional local JSON snapshot path. |
| `advisories` | no | Optional observation-only JetStream advisory subscription settings. Disabled by default and isolated from source-message ACK behavior. |
| `message_metadata` | no | Optional priority, classification, and labels extraction defaults applied to every message before sink delivery. |
| `custody` | no | Optional tamper-evident payload and metadata hashes computed by the core before sink delivery. Disabled by default. |
| `encryption` | no | Optional core payload encryption before messages are passed to any sink. |
| `pre_sink_policy` | no | Optional fail-closed validation gate evaluated after normalization and core payload transformation, but before any sink write. |
| `sink` | yes | Destination-specific sink configuration. `sink.type` chooses the sink implementation. |

The only supported `delivery.ack_policy` value is `after_sink_commit`, which
means the runner ACKs only after durable sink success or after successful DLQ
publication for permanent failures. `AckNext` is intentionally not planned for
production sink processing because it combines acknowledgement and fetching.
`AckTerm` is available only through the explicit
`dead_letter.ack_term_after_publish` option and only after DLQ publication
succeeds.

Confirmed ACK, sometimes called `AckSync` or double ACK in client APIs, has
been evaluated but is not yet a runtime configuration option. Any future option
will be disabled by default and will run only after durable sink success. See
[Acknowledgement Confirmation Evaluation](acknowledgement-confirmation.md) for
the current design direction and limitations.

JetStream `InProgress` handling has also been evaluated but is not yet a
runtime configuration option. Any future option will be disabled by default,
bounded, advisory only, and tied to verifiable consumer `AckWait` or `BackOff`
timing. See [InProgress Evaluation](in-progress-evaluation.md).

```mermaid
flowchart TD
    JSON[config.json] --> Size{At most 1 MiB?}
    Size -->|no| Error[ConfigurationError]
    Size -->|yes| Load[Read UTF-8 JSON]
    Load --> Dupes{Duplicate keys?}
    Dupes -->|yes| Error
    Dupes -->|no| Root{Root is object?}
    Root -->|no| Error[ConfigurationError]
    Root -->|yes| Env[Apply allowed environment overrides]
    Env --> Core[Validate core runtime sections]
    Core --> Sink[Validate selected sink configuration]
    Sink --> Run[Run service or print redacted effective config]
```

## Core Configuration Reference

The tables below describe every generic configuration field understood by the
core runtime. Sink-specific options are documented later in this page and in the
dedicated sink pages.

### `nats`

The `nats` section tells the runner where to connect and which JetStream stream,
consumer, and subject should feed the sink.

For mission-style subject designs, choose names that express stable operational
domains rather than transient implementation details. For example, broad
subjects can represent mission reports, logistics events, platform telemetry,
or audit events, while `message_metadata.rules` can add priority,
classification, and labels without changing producer payloads.

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `url` | no | `nats://localhost:4222` | URL using `nats`, `tls`, `ws`, or `wss`. | Single server URL passed to `nats-py` when `urls` is not set. Use `tls://` for certified encrypted TCP connections today. `ws://` and `wss://` are accepted by validation but remain evaluated, not production-certified, until the WebSocket follow-up work is implemented. Unsupported schemes fail validation. |
| `urls` | no | `[]` | Non-empty list of URLs using `nats`, `tls`, `ws`, or `wss`. | Optional seed server list for clustered deployments. When set, it is passed to `nats-py` as `servers` and takes precedence over `url`. If any seed URL uses `tls://`, or if TLS certificate files are configured, the CLI builds a TLS context. WebSocket seed lists should not mix `ws` or `wss` URLs with `nats` or `tls` URLs. |
| `stream` | yes | none | Non-empty JetStream stream name. | Stream that owns the messages consumed by the sink. |
| `consumer` | yes | none | Consumer/durable name accepted by NATS. | Durable consumer name when `durable` is true. It is also used in logging and metrics context. |
| `subject` | yes | none | NATS subject or wildcard subject, for example `orders.*` or `orders.>`. | Subject used for pull subscription binding. It should be covered by the configured stream subjects. |
| `durable` | no | `true` | `true` or `false`. | When true, binds the pull subscription as a durable consumer. Production deployments should normally keep this enabled. |
| `name` | no | `null` | Client name string. | Optional client name passed to the NATS connection. Useful for server-side connection inspection. |
| `user` | no | `null` | Username string. | Username for NATS username/password authentication. |
| `password` | no | `null` | Password string. | Direct NATS password. Use only for disposable local tests; prefer `password_env` for production. |
| `password_env` | no | `null` | Environment variable name. | Environment variable that contains the NATS password. Mutually exclusive with `password`. |
| `token` | no | `null` | Token string. | Direct NATS token. Use only for disposable local tests; prefer `token_env` for production. |
| `token_env` | no | `null` | Environment variable name. | Environment variable that contains the NATS token. Mutually exclusive with `token`. |
| `creds_file` | no | `null` | Local file path. | Path to a NATS credentials file consumed by `nats-py` as `user_credentials`. |
| `nkey_seed_file` | no | `null` | Local file path. | Path to an NKEY seed file consumed by `nats-py` as `nkeys_seed`. |
| `tls_ca_file` | no | `null` | Local file path. | CA certificate file used to trust a private or self-signed NATS server certificate. |
| `tls_cert_file` | no | `null` | Local file path. | Optional client certificate file for mutual TLS transport. |
| `tls_key_file` | no | `null` | Local file path. | Optional client private key file. Requires `tls_cert_file` when set. |
| `tls_verify` | no | `true` | `true` or `false`. | Enables certificate verification and hostname checking. Keep enabled in production. |
| `allow_reconnect` | no | `true` | `true` or `false`. | Enables `nats-py` automatic reconnect behavior after connection loss. Production deployments should normally keep this enabled. |
| `connect_timeout_seconds` | no | `2` | Integer `1` to `300`. | Initial NATS connection timeout passed as `connect_timeout`. |
| `reconnect_time_wait_seconds` | no | `2` | Integer `0` to `3600`. | Delay between reconnect attempts passed as `reconnect_time_wait`. |
| `max_reconnect_attempts` | no | `60` | Integer `-1` to `1000000`. | Maximum reconnect attempts. `-1` follows the `nats-py` convention for unlimited attempts. |
| `ping_interval_seconds` | no | `120` | Integer `1` to `3600`. | Interval for client pings used to detect unhealthy connections. |
| `max_outstanding_pings` | no | `2` | Integer `1` to `100`. | Maximum unanswered pings before the client treats the connection as unhealthy. |
| `pending_size_bytes` | no | `2097152` | Integer `1024` to `1073741824`. | Maximum pending bytes allowed by the NATS client before applying client-side pressure. |
| `drain_timeout_seconds` | no | `30` | Integer `1` to `3600`. | Timeout used by the NATS client when draining before close. |

Validation rules:

- configure either `password` or `password_env`, not both,
- configure either `token` or `token_env`, not both,
- username/password authentication requires `user` plus exactly one password
  source,
- token authentication, username/password authentication, `creds_file`, and
  `nkey_seed_file` are mutually exclusive authentication modes,
- `url` and every `urls` entry must use one of the supported NATS client
  schemes: `nats`, `tls`, `ws`, or `wss`,
- WebSocket schemes are allowed syntactically, but production-certified
  WebSocket support is tracked separately in
  [WebSocket Connection Evaluation](websocket-connection-evaluation.md),
- `tls_key_file` requires `tls_cert_file`,
- bcrypted NATS passwords are a server-side storage detail; the client still
  sends the clear-text password from `password` or `password_env`.
- `urls` entries are stripped and must not be empty or contain control
  characters.

Connection event callbacks are installed by the runner. The following metrics
are incremented when `nats-py` reports connection events:

- `nats_connection_disconnected_total`
- `nats_connection_reconnected_total`
- `nats_connection_closed_total`
- `nats_discovered_servers_total`
- `nats_async_errors_total`

If an embedding application passes its own `nats-py` callbacks through
`nats_options`, the runner wraps them so the application callback still runs
after metrics have been recorded.

Headers-only JetStream delivery is not yet a supported configuration switch in
nats-sinks. The current runtime can process empty payload bytes, but it does
not yet distinguish a producer-empty payload from a body omitted by a
headers-only consumer. See
[Headers-Only Delivery Evaluation](headers-only-delivery.md) for the design
decision and follow-up backlog items.

The `nats` section is also the source of truth for least-privilege NATS
authorization planning. `nats.stream`, `nats.consumer`, `nats.subject`, and
`dead_letter.subject` map directly to the permission placeholders described in
[NATS Least-Privilege Permissions](nats-permissions.md). Keep authentication
material such as `token_env` or `password_env` separate from authorization
design: a worker can authenticate successfully and still be denied if the NATS
server permission map does not allow the required JetStream API, inbox, ACK, or
DLQ subjects.

### `delivery`

The `delivery` section controls how the core runner fetches, writes, retries,
and ACKs messages. It is destination-neutral: Oracle, file, and future sinks all
receive batches according to these settings.

Current releases use pull consumers only. Push-consumer support has been
evaluated for a future explicit runner mode, but it is not a configuration
option yet because it needs separate manual-ACK, pending-limit, flow-control,
heartbeat, shutdown, and certification guardrails. See
[Push Consumer Evaluation](push-consumer-evaluation.md).

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `batch_size` | no | `100` | Integer `1` to `10000`. | Maximum number of messages to fetch and pass to `sink.write_batch(...)` at once. It is an upper bound, not a requirement to wait for a full batch. |
| `batch_timeout_ms` | no | `1000` | Integer greater than or equal to `1`. | Pull fetch timeout in milliseconds. Smaller values reduce latency for partial batches; larger values can improve batching efficiency. |
| `max_in_flight_batches` | no | `1` | Integer `1` to `64`. | Reserved for bounded concurrency. The current runner processes one active batch at a time to keep commit-then-ACK ordering simple and conservative. |
| `ack_policy` | no | `after_sink_commit` | Only `after_sink_commit`. | Non-negotiable commit-then-acknowledge policy. ACK happens only after the sink reports durable success or after DLQ publication succeeds for permanent failures. |
| `max_retries` | no | `5` | Integer `0` to `1000000`. | Maximum number of active delayed NAK attempts the runner will issue for retryable failures. When this budget is exhausted, the runner does not ACK and leaves the message redeliverable for JetStream consumer policy. |
| `retry_backoff_ms` | no | `1000` | Integer `0` to `3600000`. | Base delay used for retryable failures when `temporary_failure_action` is `nak`. |
| `retry_backoff_max_ms` | no | `60000` | Integer `0` to `3600000`, greater than or equal to `retry_backoff_ms`. | Maximum capped delay after fixed, linear, or exponential calculation. |
| `retry_backoff_mode` | no | `exponential` | `fixed`, `linear`, or `exponential`. | Backoff calculation mode. `fixed` always uses the base delay, `linear` multiplies the base by the one-based delivery attempt, and `exponential` multiplies by `retry_backoff_multiplier` for each redelivery attempt. |
| `retry_backoff_multiplier` | no | `2.0` | Float `1.0` to `10.0`. | Exponential multiplier used when `retry_backoff_mode` is `exponential`. Attempt `1` uses the base delay; attempt `2` uses base multiplied once. |
| `retry_jitter` | no | `full` | `none`, `full`, or `equal`. | Jitter mode applied after the delay is capped. `none` is deterministic, `full` chooses between zero and the capped delay, and `equal` chooses between half-delay and full-delay. |
| `temporary_failure_action` | no | `nak` | `nak` or `leave_unacked`. | `nak` asks JetStream to redeliver after the configured backoff. `leave_unacked` relies on the consumer ACK timeout. |
| `prefer_safe_duplication` | no | `true` | `true` or `false`. | Documents the intended reliability posture: duplicates are acceptable when idempotency handles them; silent loss is not. Keep true unless a future sink documents a reviewed alternative. |
| `priority_lanes` | no | disabled default lane | Object. | Optional in-batch priority scheduling policy. See [Priority-Aware Processing Lanes](priority-lanes.md). |

Retry delays are based on JetStream delivery-attempt metadata when available.
For example, with the defaults, the first retryable failure uses a base delay
of up to one second after jitter, the second delivery can use up to two seconds,
the third up to four seconds, and so on until the capped delay is reached.
Jitter is enabled by default so many sink instances do not retry in lockstep
after a shared Oracle, filesystem, or network outage. This is especially useful
in controlled networks where a temporary dependency outage can affect many
consumers at once.

When `max_retries` is reached, the runner still does not ACK the message.
Instead, it stops issuing active NAKs for that failure and leaves the message
redeliverable according to the configured JetStream consumer policy. This keeps
the framework aligned with commit-then-acknowledge while avoiding an endless
client-side retry loop.

#### `delivery.priority_lanes`

Priority lanes let the runner reorder an already-fetched batch before calling
`sink.write_batch(...)`. They are disabled by default. When enabled, each
message is assigned to a configured lane based on normalized
`NatsEnvelope.priority`, and the active batch is emitted with deterministic
weighted round-robin.

Priority lanes do not change JetStream server-side delivery order, do not
provide strict total ordering, and do not change ACK behavior. The runner still
ACKs only after the sink reports durable success for the batch.

```json
{
  "delivery": {
    "priority_lanes": {
      "enabled": true,
      "default_lane": "routine",
      "unknown_priority_action": "default_lane",
      "max_priority_value_length": 64,
      "lanes": [
        {
          "name": "urgent",
          "priorities": ["urgent", "immediate"],
          "weight": 3
        },
        {
          "name": "routine",
          "priorities": ["normal", "routine"],
          "weight": 1
        }
      ]
    }
  }
}
```

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `enabled` | no | `false` | `true` or `false`. | Enables in-batch priority scheduling. Disabled keeps fetched order unchanged. |
| `default_lane` | no | `default` | Configured lane name. | Lane used when priority is missing or, by default, unknown. |
| `unknown_priority_action` | no | `default_lane` | `default_lane` or `reject`. | Unknown but syntactically safe priorities can be downgraded to the default lane or rejected as permanent validation failures. |
| `max_priority_value_length` | no | `64` | Integer `1` to `256`. | Maximum accepted message priority length when lanes are enabled. |
| `lanes[].name` | yes | none | Lowercase lane name up to 64 characters. | Lane identifier. Use non-sensitive names because configuration and diagnostics may mention them. |
| `lanes[].priorities` | no | `[]` | String or string list. | Case-insensitive priority values mapped to the lane. A priority may appear in only one lane. |
| `lanes[].weight` | no | `1` | Integer `1` to `100`. | Weighted round-robin share inside a mixed batch. |

Priority values can be defaulted globally or by subject pattern through
`message_metadata.rules`. This lets operators assign urgency by subject family
without trusting every publisher to set a priority header.

For the full design, limitations, starvation controls, and metrics, read
[Priority-Aware Processing Lanes](priority-lanes.md).

### `dead_letter`

The `dead_letter` section controls what happens to permanently invalid messages,
for example malformed payloads when a sink is configured with
`payload_mode: "json_only"` or a message that lacks required idempotency
metadata. DLQ publication follows the same safety rule: the original message is
ACKed only after DLQ publication succeeds.

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `enabled` | no | `false` | `true` or `false`. | Enables DLQ publication for permanent failures. |
| `subject` | required when enabled | `null` | NATS subject. | Subject where DLQ messages are published. Required when `enabled` is true. |
| `include_payload` | no | `true` | `true` or `false`. | Includes the original message body in the DLQ payload. Disable when payload privacy is more important than DLQ replay convenience. |
| `include_headers` | no | `true` | `true` or `false`. | Includes original message headers in the DLQ payload. Disable if headers may contain sensitive values. |
| `include_error` | no | `true` | `true` or `false`. | Includes framework error type and message in the DLQ payload. |
| `ack_term_after_publish` | no | `false` | `true` or `false`. | When true, sends JetStream `AckTerm` after DLQ publication succeeds instead of normal ACK. This is disabled by default, applies only to permanent failures with successful DLQ publication, and must not be used to signal successful sink writes. |

### `logging`

The `logging` section configures Python standard logging. It does not enable
payload logging by itself; payload visibility is controlled by the separate
`payload_logging` flag.

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `level` | no | `INFO` | Standard levels such as `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. | Minimum log level configured by the CLI before the runner starts. Can be overridden by `NATS_SINKS_LOG_LEVEL` or CLI `--log-level`. |
| `payload_logging` | no | `false` | `true` or `false`. | Reserved privacy switch for code paths that may log payload details. Keep false in production. |

### `metrics`

The `metrics` section prepares the service for metrics emission while keeping
the default runtime dependency surface small. The built-in `nats-sink run`
command uses a no-op metrics recorder unless `metrics.enabled` is true and
`metrics.snapshot_file` is configured. Embedding applications can also supply a
custom recorder directly through the Python API. This avoids opening extra
ports or adding exporter dependencies by surprise.

When a snapshot file is configured, the runner writes a local JSON document
that can be inspected with the separate `nats-sink-metrics` CLI. The snapshot
does not contain payloads or secrets, but it can reveal operational tempo and
failure rates, so store it in an operator-controlled path.

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `enabled` | no | `false` | `true` or `false`. | Enables metrics when a snapshot file is configured or a concrete recorder/exporter is supplied by deployment code. |
| `namespace` | no | `nats_sinks` | Letters, digits, underscores, and colons. Must not start with a digit. | Prefix used by metrics integrations and documentation. Exporters should combine this namespace with emitted suffixes, for example `nats_sinks_messages_fetched_total`. |
| `snapshot_file` | no | `null` | Local filesystem path without control characters. | Optional JSON snapshot file written by `nats-sink run` when metrics are enabled. The `nats-sink-metrics` CLI reads this file for table, JSON, JSONL, shell, names, and Prometheus text output. |

Preferred metric suffixes are documented in
[Metrics](metrics.md) and summarized in [Operations](operations.md#metrics).
The runner also emits a small set of legacy aliases so existing local
dashboards can migrate gradually.

External sharing is configured separately through an observability policy, not
inside the sink runtime config. Use [Observability](observability.md) for the
policy model and its [Prometheus Integration](prometheus.md) sub-page when you
want to publish only approved metric names to node_exporter's textfile
collector or to the optional native Prometheus HTTP endpoint. The default
generated policy keeps all Prometheus sharing disabled. The same observability
policy also controls the optional NATS
server monitoring connector for selected `/healthz`, `/jsz`, and related
endpoint fields. That connector is separate from `nats-sink run` and must be
enabled explicitly through `nats_server_monitoring`.

Example:

```json
{
  "metrics": {
    "enabled": true,
    "namespace": "nats_sinks",
    "snapshot_file": ".local/nats-sinks/metrics.json"
  }
}
```

Inspect the snapshot:

```bash
nats-sink-metrics show .local/nats-sinks/metrics.json --format table
nats-sink-metrics get .local/nats-sinks/metrics.json messages_failed_total --default 0
```

### `advisories`

The `advisories` section enables optional observation of selected JetStream
server advisories. JetStream publishes advisories as normal NATS messages below
`$JS.EVENT.ADVISORY.>`. The official
[NATS JetStream monitoring documentation](https://docs.nats.io/running-a-nats-service/nats_admin/monitoring/monitoring_jetstream#advisories)
lists these advisories as operational events covering API interactions, stream
and consumer actions, maximum-delivery signals, NAKs, terminal
acknowledgements, and clustered leadership or quorum changes.

This feature is disabled by default. When enabled, the runner creates separate
Core NATS subscriptions for the configured advisory subjects. Advisory messages
are parsed only to classify them into fixed, low-cardinality metric counters.
The runner does not store advisory payloads, does not export stream or consumer
names as metric labels, does not write advisories to a sink, and does not use
advisories to decide whether a source message should be ACKed.

```mermaid
sequenceDiagram
    participant NATS as NATS JetStream Server
    participant Adv as Advisory Observer
    participant Metrics as MetricsRecorder
    participant Runner as Sink Runner
    participant Sink as Destination Sink

    NATS-->>Adv: $JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES...
    Adv->>Adv: validate subject and bounded JSON payload
    Adv->>Metrics: increment aggregate advisory counter
    Runner->>Sink: normal message batch
    Sink-->>Runner: durable success
    Runner-->>NATS: ACK original message after sink success
```

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `enabled` | no | `false` | `true` or `false`. | Enables the observation-only advisory subscriber. Keep disabled unless the NATS account has explicit advisory-read permissions and operators need the counters. |
| `subjects` | no | Supported advisory subjects. | List of NATS subject patterns starting with `$JS.EVENT.ADVISORY.`. Up to 32 entries. | Advisory subjects to subscribe to. Duplicate subjects, non-advisory subjects, padded strings, control characters, and invalid wildcard patterns fail validation. |
| `max_payload_bytes` | no | `65536` | Integer `0` to `1048576`. | Maximum advisory JSON payload size accepted by the observer before it increments the parse-error counter. |
| `log_events` | no | `false` | `true` or `false`. | Emits sanitized one-line advisory-kind logs. Payload bodies, stream names, consumer names, and sequence numbers are still not logged by this observer. |

The built-in default subject list covers:

- `$JS.EVENT.ADVISORY.API`
- `$JS.EVENT.ADVISORY.API.>`
- `$JS.EVENT.ADVISORY.STREAM.CREATED.*`
- `$JS.EVENT.ADVISORY.STREAM.DELETED.*`
- `$JS.EVENT.ADVISORY.STREAM.MODIFIED.*`
- `$JS.EVENT.ADVISORY.STREAM.LEADER_ELECTED.*`
- `$JS.EVENT.ADVISORY.STREAM.QUORUM_LOST.*`
- `$JS.EVENT.ADVISORY.CONSUMER.CREATED.*.*`
- `$JS.EVENT.ADVISORY.CONSUMER.DELETED.*.*`
- `$JS.EVENT.ADVISORY.CONSUMER.MODIFIED.*.*`
- `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*`
- `$JS.EVENT.ADVISORY.CONSUMER.MSG_NAKED.*.*`
- `$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.*.*`
- `$JS.EVENT.ADVISORY.CONSUMER.LEADER_ELECTED.*.*`
- `$JS.EVENT.ADVISORY.CONSUMER.QUORUM_LOST.*.*`

Example:

```json
{
  "advisories": {
    "enabled": true,
    "subjects": [
      "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",
      "$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.*.*"
    ],
    "max_payload_bytes": 32768,
    "log_events": false
  }
}
```

Required NATS permissions are separate from the normal sink permissions. The
runtime account needs subscribe rights for the configured advisory subjects
only. It does not need JetStream management rights merely to observe
advisories. See [NATS Least-Privilege Permissions](nats-permissions.md) for a
permission template.

### `message_metadata`

The `message_metadata` section defines three destination-neutral metadata
fields that the core runtime places on every `NatsEnvelope`: `priority`,
`classification`, and `labels`. These fields are useful when operators want to
preserve business urgency, information classification, and searchable tags
alongside the payload without making every sink implement its own header
parsing rules.

`nats-sinks` treats these values as operator-defined strings. It does not
enforce a classification scheme or priority vocabulary. Defence and
mission-oriented examples in this documentation use NATO-style classification
strings such as `NATO UNCLASSIFIED`, `NATO RESTRICTED`, `NATO CONFIDENTIAL`,
`NATO SECRET`, and `COSMIC TOP SECRET`. Use the exact values required by your
organization's policy, markings, and release process.

All three fields are optional. When priority or classification is missing after
resolution, nats-sinks stores it as null: JSON sinks write JSON `null`, and
Oracle writes SQL `NULL`. Labels are normalized as an immutable list in the
core; scalar sink fields store labels as semicolon-separated text such as
`billing;urgent`, while metadata documents store the label list. The literal
string `"null"` is not written by the framework.

Resolution order for each field is:

1. If the configured NATS header is present and non-empty, use that header
   value.
2. If the configured NATS header is present but empty or whitespace-only, store
   null for that message.
3. If the configured NATS header is absent, evaluate `message_metadata.rules`
   in order and use the first matching subject-specific default for that field.
4. If no subject rule supplies a default for that field, use the global
   configured default.
5. If the selected default is absent or empty, store null.

```mermaid
flowchart TD
    Start[Message arrives] --> Header{Configured header present?}
    Header -->|yes, non-empty| UseHeader[Use header value]
    Header -->|yes, empty| Null[Store null]
    Header -->|no| Rules{Subject rule default?}
    Rules -->|yes| UseRule[Use subject default]
    Rules -->|no| Default{Global default configured?}
    Default -->|yes, non-empty| UseDefault[Use global default]
    Default -->|no or empty| Null
```

Default header names:

- `Nats-Sinks-Priority`
- `Nats-Sinks-Classification`
- `Nats-Sinks-Labels`

Configuration shape:

```json
{
  "message_metadata": {
    "priority": {
      "header": "Nats-Sinks-Priority",
      "default": "routine"
    },
    "classification": {
      "header": "Nats-Sinks-Classification",
      "default": "NATO UNCLASSIFIED"
    },
    "labels": {
      "header": "Nats-Sinks-Labels",
      "default": "logistics;default"
    },
    "rules": [
      {
        "subject": "mission.reports.>",
        "priority": "immediate",
        "classification": "NATO SECRET",
        "labels": "mission-report;coalition;watch-floor"
      },
      {
        "subject": "public.>",
        "priority": "routine",
        "classification": "NATO UNCLASSIFIED",
        "labels": "public;training"
      }
    ]
  }
}
```

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `priority.header` | no | `Nats-Sinks-Priority` | Non-empty header name without newlines. | Header read from each NATS message to populate `NatsEnvelope.priority`. |
| `priority.default` | no | `null` | String or `null`. | Value used when the priority header is absent. Blank strings become null. |
| `classification.header` | no | `Nats-Sinks-Classification` | Non-empty header name without newlines. | Header read from each NATS message to populate `NatsEnvelope.classification`. |
| `classification.default` | no | `null` | String or `null`. | Value used when the classification header is absent. Blank strings become null. |
| `labels.header` | no | `Nats-Sinks-Labels` | Non-empty header name without newlines. | Header read from each NATS message to populate `NatsEnvelope.labels`. |
| `labels.default` | no | `[]` | Semicolon-separated string, JSON string list, `[]`, or `null`. | Labels used when the labels header is absent. Empty items are removed and duplicate labels are ignored after their first occurrence. |
| `rules` | no | `[]` | List of subject-rule objects. | Ordered subject-specific defaults. First matching rule that sets the requested field wins; unmatched subjects use the global default. |

Subject rules use NATS wildcard syntax. `*` matches exactly one token and `>`
matches one or more remaining tokens only when it is the final token.

| Rule field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `subject` | yes | none | NATS subject pattern, for example `orders.*` or `orders.>`. | Pattern matched against `NatsEnvelope.subject`. |
| `priority` | no | unset | String or `null`. | Subject-specific priority default used only when the priority header is absent. Explicit `null` overrides the global default for matching subjects. |
| `classification` | no | unset | String or `null`. | Subject-specific classification default used only when the classification header is absent. Explicit `null` overrides the global default for matching subjects. |
| `labels` | no | unset | Semicolon-separated string, JSON string list, `[]`, or `null`. | Subject-specific label default used only when the labels header is absent. Explicit `null` or `[]` overrides the global label default with no labels for matching subjects. |

At least one of `priority`, `classification`, or `labels` must be present in a
rule. Rule order is significant. Put more specific subject defaults before
broader patterns when both could match the same message.

Example publish command:

```bash
nats pub mission.reports.created '{"report_id":"R-1001","status":"received"}' \
  -H 'Nats-Sinks-Priority: immediate' \
  -H 'Nats-Sinks-Classification: NATO SECRET' \
  -H 'Nats-Sinks-Labels: mission-report;coalition;watch-floor'
```

The resulting `NatsEnvelope` has:

```json
{
  "priority": "immediate",
  "classification": "NATO SECRET",
  "labels": ["mission-report", "coalition", "watch-floor"]
}
```

File sinks store `labels` both as semicolon-separated text and as
`labels_list`; Oracle stores the scalar value in the `LABELS` column and the
list in the generic `METADATA_JSON` document.

For richer operational or mission-support context, use the optional
`mission_metadata` section described below. It carries one validated JSON
object through the core runtime so Oracle can store it in
`MISSION_METADATA_JSON`, file sink records can expose it as top-level
`mission_metadata`, and future sinks can preserve the same destination-neutral
context without adding many fixed framework fields.

### `mission_metadata`

The `mission_metadata` section is disabled by default. Enable it when messages
need a richer JSON context object such as mission ID, operation ID, platform
ID, source system, sensor ID, track ID, correlation ID, confidence,
releasability, domain, or a use-case-specific lifecycle marker.

```json
{
  "mission_metadata": {
    "enabled": true,
    "header": "Nats-Sinks-Mission-Metadata",
    "max_bytes": 8192,
    "allowed_profiles": ["mission-event-v1"],
    "default": {
      "profile": "mission-event-v1",
      "profile_version": 1,
      "origin_domain": "operations"
    },
    "rules": [
      {
        "subject": "mission.synthetic.>",
        "metadata": {
          "profile": "mission-event-v1",
          "profile_version": 1,
          "origin_domain": "synthetic-test"
        }
      }
    ]
  }
}
```

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `enabled` | no | `false` | `true` or `false`. | Enables parsing, validation, and sink delivery of mission metadata. Disabled runners ignore the configured header. |
| `header` | no | `Nats-Sinks-Mission-Metadata` | Non-empty header name without control characters. | Header containing a JSON object supplied by a publisher. |
| `max_bytes` | no | `8192` | Integer from `1` to `262144`. | Maximum canonical JSON size for one mission metadata object. |
| `allowed_profiles` | no | `[]` | List of non-empty strings. | Optional profile allow-list. When set, the metadata object must contain `profile` with one of these values. |
| `default` | no | `null` | JSON object or `null`. | Global default used when the header is absent and no subject rule matches. |
| `rules` | no | `[]` | List of subject-rule objects. | Ordered subject-specific defaults evaluated before the global default. |

Rule fields:

| Rule field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `subject` | yes | none | NATS subject pattern. | Subject pattern matched against `NatsEnvelope.subject`. |
| `metadata` | yes | none | JSON object or `null`. | Default mission metadata for matching subjects. `null` explicitly clears the global default. |

Publisher-provided mission metadata uses the configured header:

```bash
nats pub mission.synthetic.sensor.track.0001 '{"event_id":"SYN-0001"}' \
  -H 'Nats-Sinks-Mission-Metadata: {"profile":"mission-event-v1","mission_id":"SYN-MISSION-001","f2t2ea_phase":"track"}'
```

Mission metadata is validated before sink delivery. Invalid JSON, duplicate
keys, secret-looking key names, unsupported value types, control characters,
oversized objects, and profile allow-list violations are permanent validation
failures and follow DLQ-before-ACK semantics when DLQ is configured.

See [Mission Metadata](mission-metadata.md) for the full storage contract and
[F2T2EA Event Phase Tagging](use-cases/defence/f2t2ea-event-phase-tagging.md)
for a defence-oriented use-case blueprint built on the generic feature.

### `encryption`

The `encryption` section is a generic core runtime feature. When enabled, the
runner encrypts `NatsEnvelope.data` before calling `sink.write_batch(...)`.
Metadata remains unencrypted so routing, idempotency, timestamps, headers,
subject names, and stream sequence values continue to work in every sink.

Payload encryption is optional and disabled by default. Install the crypto
extra before enabling it:

```bash
pip install "nats-sinks[crypto]"
```

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `enabled` | no | `false` | `true` or `false`. | Enables core payload encryption before sink delivery. |
| `algorithm` | no | `aes-256-gcm` | `aes-256-gcm` or `aes-256-ccm`. Uppercase spellings such as `AES-256-GCM` are accepted and normalized. | Authenticated encryption algorithm used for payload bytes. |
| `key_id` | no | `default` | Non-empty string up to 128 characters. | Non-secret identifier stored in the encrypted payload envelope so operators can identify which key was used. |
| `key_b64` | no | `null` | Base64 text that decodes to exactly 32 bytes. | Direct AES-256 key material. Use only for disposable local tests; prefer `key_b64_env` for production. Redacted by CLI output. |
| `key_b64_env` | no | `null` | Environment variable name. | Environment variable containing base64-encoded 32-byte AES key material. Recommended production shape. Mutually exclusive with `key_b64`. |
| `nonce_size_bytes` | no | `12` | Integer `7` to `13`. | Random nonce size for each message. The default works for both AES-GCM and AES-CCM. |
| `tag_length` | no | `16` | `4`, `6`, `8`, `10`, `12`, `14`, or `16`. | Authentication tag length used by AES-CCM. AES-GCM records `16`; the setting is accepted but does not change GCM tag length. |
| `rules` | no | `[]` | List of subject-rule objects. | Optional ordered per-subject encryption policy. First matching rule wins. Subjects with no matching rule use the top-level `enabled` setting. |

Example:

```json
{
  "encryption": {
    "enabled": true,
    "algorithm": "aes-256-gcm",
    "key_id": "orders-prod-2026-05",
    "key_b64_env": "NATS_SINKS_PAYLOAD_KEY_B64",
    "nonce_size_bytes": 12,
    "tag_length": 16
  }
}
```

Subject-specific rules let one runner encrypt only selected subjects or use
different keys for different subject families. The following configuration
encrypts `secure.>` but stores `public.>` and all other subjects without core
payload encryption:

```json
{
  "encryption": {
    "enabled": false,
    "rules": [
      {
        "subject": "secure.>",
        "enabled": true,
        "algorithm": "aes-256-gcm",
        "key_id": "secure-prod-2026-05",
        "key_b64_env": "NATS_SINKS_SECURE_PAYLOAD_KEY_B64"
      }
    ]
  }
}
```

The next configuration encrypts all subjects by default and uses a disabled
rule to exempt `public.>`:

```json
{
  "encryption": {
    "enabled": true,
    "algorithm": "aes-256-gcm",
    "key_id": "global-prod-2026-05",
    "key_b64_env": "NATS_SINKS_GLOBAL_PAYLOAD_KEY_B64",
    "rules": [
      {
        "subject": "public.>",
        "enabled": false
      }
    ]
  }
}
```

Subject rules use NATS wildcard syntax. `*` matches exactly one token and `>`
matches one or more remaining tokens only when it is the final token. Rule order
is significant and the first matching rule wins.

For key rotation, change the active `key_id` and key source for new messages,
then keep previous key material available to authorized decryption tooling for
the full replay and retention window. The runtime encrypts with the configured
active key only; operational tools can use the public `PayloadKeyRegistry`
helper to decrypt records written across multiple key generations. See
[Payload Encryption](payload-encryption.md) for the full rotation pattern and
secret-manager bootstrap guidance.

| Rule field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `subject` | yes | none | NATS subject pattern, for example `orders.*` or `secure.>`. | Pattern matched against the normalized envelope subject. |
| `enabled` | no | `true` | `true` or `false`. | Enables encryption for matching subjects, or disables encryption when used as an exemption. |
| `algorithm` | no | Top-level `algorithm`. | `aes-256-gcm` or `aes-256-ccm`. | Optional algorithm override for the matching subject. |
| `key_id` | no | Top-level `key_id`. | Non-empty string up to 128 characters. | Optional non-secret key identifier override. |
| `key_b64` | no | Top-level `key_b64`. | Base64 text that decodes to exactly 32 bytes. | Optional direct key material for this subject rule. Redacted by CLI output. |
| `key_b64_env` | no | Top-level `key_b64_env`. | Environment variable name. | Optional environment-backed key source for this subject rule. |
| `nonce_size_bytes` | no | Top-level `nonce_size_bytes`. | Integer `7` to `13`. | Optional nonce size override for this subject rule. |
| `tag_length` | no | Top-level `tag_length`. | `4`, `6`, `8`, `10`, `12`, `14`, or `16`. | Optional AES-CCM tag length override for this subject rule. |

The environment variable value must be a base64-encoded 32-byte key:

```bash
python -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'
```

The encrypted body stored by sinks is a JSON object under
`_nats_sinks_encryption`. It contains the algorithm, key identifier, nonce,
ciphertext, tag length, plaintext size, and plaintext SHA-256 digest. It does
not contain the plaintext message body. See [Payload Encryption](payload-encryption.md)
for the full envelope shape, examples, testing guidance, and operational
security notes.

### `custody`

The `custody` section enables optional tamper-evident evidence computed by the
core before sink delivery. It is disabled by default because hashes can still
reveal repeated payloads or repeated metadata patterns. When enabled, the runner
computes a custody object, attaches it to `NatsEnvelope`, and every production
sink persists it next to the durable record.

Custody metadata is a pre-sink operation. If the core cannot compute it because
the configured algorithm is invalid, a previous hash is malformed, or the
canonical hash input exceeds the configured size limit, the sink is not called.
The failure is treated as a permanent validation failure and follows the
DLQ-before-ACK path when DLQ is enabled.

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `enabled` | no | `false` | `true` or `false`. | Enables custody metadata computation before sink writes. |
| `algorithm` | no | `sha256` | `sha256`, `sha512`. | Hash algorithm used for payload, metadata, and record hashes. |
| `hash_payload` | no | `true` | `true` or `false`. | Hashes the normalized payload storage value. |
| `hash_metadata` | no | `true` | `true` or `false`. | Hashes stable generic metadata. Sink-local storage timestamps are excluded. |
| `include_previous_hash` | no | `false` | `true` or `false`. | Reads an optional previous-record hash from the configured header. |
| `previous_hash_header` | no | `Nats-Sinks-Previous-Custody-Hash` | Header name without control characters. | Header used for optional hash chaining. Missing values are accepted; malformed values fail closed. |
| `key_id` | no | `null` | Non-secret text up to 128 characters. | Optional policy or future key-version identifier. Do not store key material here. |
| `max_hash_input_bytes` | no | `16777216` | Integer `1024` to `1073741824`. | Maximum canonical JSON byte length accepted for each hash input. |

Example:

```json
{
  "custody": {
    "enabled": true,
    "algorithm": "sha256",
    "hash_payload": true,
    "hash_metadata": true,
    "key_id": "custody-policy-v1"
  }
}
```

The persisted object includes fields such as `payload_hash`, `metadata_hash`,
`record_hash`, and `previous_record_hash`. Hashes are not encryption and are
not digital signatures. For the full model, examples, privacy guidance, and
sink storage behavior, read
[Tamper-Evident Custody Metadata](tamper-evident-custody.md).

### `pre_sink_policy`

The `pre_sink_policy` section is an optional core runtime gate. It is disabled
by default because not every deployment needs a policy layer. When enabled, it
runs after the runner has normalized the message, resolved priority,
classification, labels, mission metadata, and optional payload encryption, but
before `sink.write_batch(...)` is called.

This means the rule is sink-neutral: the same policy protects Oracle, file, and
future sinks. A rejected message never reaches a sink. Policy rejection is a
permanent validation failure. If DLQ is enabled, the rejected message is
published to DLQ and the original JetStream message is ACKed or terminally
acknowledged only after DLQ publication succeeds. If DLQ publication fails, the
original message is not ACKed.

The policy engine intentionally does not support Python code, dynamic imports,
regular expressions, templates, or a general expression language. Supported
checks are explicit allow-listed fields that are easy to review:

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `enabled` | no | `false` | `true` or `false`. | Enables the pre-sink policy gate. When true, at least one rule is required. |
| `unmatched_subject_action` | no | `reject` | `reject` or `allow`. | What happens when the gate is enabled and no rule matches a message subject. The secure default rejects unmatched subjects. |
| `rules` | no | `[]` | List of rule objects. | Subject-scoped checks. All matching rules apply, so a global rule and a subject-specific rule can both constrain the same message. |

Rule fields:

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `subject` | no | `>` | NATS subject pattern such as `orders.*` or `orders.secure.>`. | Subjects matched by this rule. |
| `require_priority` | no | `false` | `true` or `false`. | Requires `NatsEnvelope.priority` to be present after `message_metadata` resolution. |
| `require_classification` | no | `false` | `true` or `false`. | Requires `NatsEnvelope.classification` to be present. |
| `required_labels` | no | `[]` | String with semicolon-separated labels or a JSON array of strings. | Requires all listed labels to be present in `NatsEnvelope.labels`. |
| `require_mission_metadata` | no | `false` | `true` or `false`. | Requires a validated mission metadata object. |
| `require_encrypted_payload` | no | `false` | `true` or `false`. | Requires the payload delivered to the sink to be the standard nats-sinks encrypted payload envelope. |
| `max_payload_bytes` | no | `null` | Integer `0` to `1073741824`. | Rejects messages whose current sink-bound payload bytes exceed this size. If encryption is enabled, this checks encrypted envelope size. |
| `allowed_mission_metadata_keys` | no | `null` | JSON array of safe root key names. | If set, every root key in mission metadata must be in this allow list. An empty list means no mission metadata keys are allowed. Secret-looking key names are rejected in configuration. |

Example: require classification and encrypted payloads for secure operational
events, while still allowing routine order events through a less restrictive
rule:

```json
{
  "pre_sink_policy": {
    "enabled": true,
    "rules": [
      {
        "subject": "orders.secure.>",
        "require_priority": true,
        "require_classification": true,
        "required_labels": ["orders", "audit"],
        "require_mission_metadata": true,
        "require_encrypted_payload": true,
        "allowed_mission_metadata_keys": ["profile", "phase", "operation"]
      },
      {
        "subject": "orders.routine.*",
        "require_classification": true,
        "max_payload_bytes": 1048576
      }
    ]
  }
}
```

The gate emits policy metrics when enabled:

- `policy_messages_passed_total`,
- `policy_messages_rejected_total`,
- `policy_batches_passed_total`,
- `policy_batches_rejected_total`,
- `policy_evaluation_errors_total`.

Read [Metrics](metrics.md) for CLI examples and
[Dead Letter Queues](dead-letter-queues.md) for the ACK-after-DLQ rule.

### `sink`

The `sink` section selects the destination and carries all destination-specific
options. The core validates `sink.type`, then the safe sink registry passes the
remaining fields to the selected sink validator.

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `type` | yes | none | `file` or `oracle` in the current release. | Selects the production sink implementation. Future sinks should add new values without changing the generic core sections. |

All other fields under `sink` are sink-specific:

- `file` fields are documented in [File Sink](file-sink.md),
- `oracle` fields are documented in [Oracle Sink](oracle-sink.md).

## Delivery Settings

The `delivery.batch_size` value is a maximum fetch and write size, not a
minimum. The runner asks JetStream for up to that many messages and also passes
`delivery.batch_timeout_ms` to the pull request. When fewer messages are
available, the NATS client can return a smaller batch after the timeout, and the
runner writes that partial batch immediately.

For example, with `batch_size=64`, a final batch of 58 messages is valid and is
written, committed, and ACKed just like a full batch. This keeps low-volume
streams from waiting indefinitely while still allowing larger batches when
traffic is available.

```mermaid
flowchart LR
    Fetch[Fetch up to batch_size] --> Available{64 messages available?}
    Available -->|yes| Full[Write full batch]
    Available -->|no, timeout expires with data| Partial[Write partial batch]
    Full --> Commit[Commit sink transaction]
    Partial --> Commit
    Commit --> Ack[ACK processed messages]
```

## Sink-Specific Configuration

The top-level configuration model validates the generic runtime sections
strictly and leaves `sink` fields to the selected sink implementation. This is
what lets the project add future sinks without changing the stable core
configuration shape:

```mermaid
flowchart LR
    Config[config.json] --> Core[core sections: nats, delivery, logging]
    Config --> Sink[sink object]
    Sink --> Type[sink.type]
    Type --> Registry[explicit sink registry]
    Registry --> Validator[sink-specific validator]
```

Every sink must define its own documented JSON fields, validation rules,
secret-handling guidance, and examples. The current production sinks are:

- `"type": "oracle"` for Oracle Database. Detailed Oracle connection options,
  Autonomous Database wallet settings, table routing, payload modes, and column
  mappings live in [Oracle Sink](oracle-sink.md).
- `"type": "file"` for local JSON file output. File durability, duplicate
  policies, deterministic file names, optional gzip compression, and filesystem safety live in
  [File Sink](file-sink.md).

This separation is part of the compatibility contract. Adding a future
`postgres`, `http`, or `s3` sink should add new sink-specific fields under
`"sink"` without requiring existing Oracle or file users to change the rest of
their configuration.

## Payload Storage Modes

NATS message bodies are bytes. The framework-level payload normalization
contract lets JSON-capable sinks store both JSON and non-JSON bodies safely,
but the exact destination field names belong to each sink.

The shared payload modes are:

| Value | Meaning |
| --- | --- |
| `json_or_envelope` | Default for JSON-capable sinks. Store standards-compliant JSON unchanged; wrap non-JSON text or bytes in the nats-sinks JSON payload envelope. Python-only constants such as `NaN`, `Infinity`, and `-Infinity` are treated as non-JSON text. |
| `json_only` | Require standards-compliant JSON. Non-JSON bodies, malformed JSON, and Python-only constants such as `NaN` become permanent serialization failures and may go to DLQ. |
| `text_envelope` | Treat every body as UTF-8 text and wrap it in the JSON envelope. Use this for encrypted text streams. |
| `bytes_envelope` | Treat every body as bytes and wrap base64 content in the JSON envelope. |

Future sinks should either reuse these modes or document a deliberate,
well-tested alternative. See [Sink Framework](sink-framework.md) for the
destination-neutral payload envelope, [Oracle Sink](oracle-sink.md) for the
Oracle implementation, and [File Sink](file-sink.md) for local file output.

## Metadata Storage

`NatsEnvelope.metadata_for_json_storage()` produces a generic metadata document
that every sink can persist. The document includes all headers,
NATS-reserved headers when present, unknown future `Nats-` headers, JetStream
sequence metadata, optional reply subject, and timestamp fields.
It also includes the normalized application-level message metadata fields:

```json
{
  "message_metadata": {
    "priority": "urgent",
    "classification": "restricted",
    "labels": ["billing", "urgent"]
  }
}
```

Missing optional NATS headers are allowed and do not make the message invalid.
Destination-specific docs should explain whether the metadata is stored as one
document, split into columns, or mapped into another backend-native structure.

## Environment Overrides

Supported environment overrides:

- `NATS_SINKS_NATS_URL`
- `NATS_SINKS_NATS_STREAM`
- `NATS_SINKS_NATS_CONSUMER`
- `NATS_SINKS_NATS_SUBJECT`
- `NATS_SINKS_LOG_LEVEL`
- `NATS_SINKS_ENCRYPTION_ENABLED`
- `NATS_SINKS_ENCRYPTION_ALGORITHM`
- `NATS_SINKS_ENCRYPTION_KEY_ID`
- `NATS_SINKS_ENCRYPTION_KEY_B64_ENV`
- `NATS_SINKS_PRIORITY_HEADER`
- `NATS_SINKS_PRIORITY_DEFAULT`
- `NATS_SINKS_CLASSIFICATION_HEADER`
- `NATS_SINKS_CLASSIFICATION_DEFAULT`
- `NATS_SINKS_LABELS_HEADER`
- `NATS_SINKS_LABELS_DEFAULT`
- `NATS_SINKS_MISSION_METADATA_ENABLED`
- `NATS_SINKS_MISSION_METADATA_HEADER`
- `NATS_SINKS_CUSTODY_ENABLED`
- `NATS_SINKS_CUSTODY_ALGORITHM`
- `NATS_SINKS_CUSTODY_KEY_ID`
- `NATS_SINKS_ADVISORIES_ENABLED`
- `NATS_SINKS_SINK_TYPE`

Destination passwords should normally be supplied through environment variables
referenced by the selected sink configuration, for example `sink.password_env`
for sinks that use password-based authentication.

NATS passwords and tokens should normally be supplied through `nats.password_env`
or `nats.token_env`. Direct `nats.password` and `nats.token` values are useful
for disposable local tests but should not be committed.

## NATS Authentication And TLS

For NATS token authentication:

```json
{
  "nats": {
    "url": "tls://nats.example.com:4222",
    "stream": "ORDERS",
    "consumer": "orders-sink",
    "subject": "orders.*",
    "token_env": "NATS_TOKEN",
    "tls_ca_file": "/etc/nats/certs/ca.crt"
  }
}
```

For plain username/password or server-side bcrypted username/password:

```json
{
  "nats": {
    "url": "tls://nats.example.com:4222",
    "stream": "ORDERS",
    "consumer": "orders-sink",
    "subject": "orders.*",
    "user": "orders_sink",
    "password_env": "NATS_PASSWORD",
    "tls_ca_file": "/etc/nats/certs/ca.crt"
  }
}
```

In the bcrypted case, the bcrypt hash belongs in the NATS server
configuration. The client still supplies the clear-text password from
`NATS_PASSWORD`, and TLS protects that credential in transit.

Choose one NATS authentication mode per configuration. For example, do not mix
`token_env` with `user` and `password_env`, and do not combine `creds_file`
with token or username/password fields. The validator fails closed before the
runner connects to NATS.

For detailed connection guidance, see
[NATS Connections And Authentication](nats-connections.md).

## Logging

`nats-sinks` uses Python's standard logging levels. The default level is
`INFO`, which is intended to be useful for normal service operation without
printing sensitive message payloads or credentials.

The log level is allow-listed. Unknown levels fail configuration validation
instead of silently falling back to a different policy. Log records emitted
through the CLI formatter also escape control characters, newlines, carriage
returns, tabs, and terminal escape sequences so untrusted subjects, headers, or
driver messages cannot forge extra log lines.

Configure the level in JSON:

```json
{
  "logging": {
    "level": "INFO",
    "payload_logging": false
  }
}
```

You can also override the level at runtime with `NATS_SINKS_LOG_LEVEL` or with
the CLI `--log-level` option:

```bash
NATS_SINKS_LOG_LEVEL=DEBUG nats-sink run config.json
nats-sink run config.json --log-level WARNING
```

| Level | Intended use |
| --- | --- |
| `DEBUG` | Detailed troubleshooting during development or controlled support sessions. Avoid in production unless you have reviewed what the active code path can log. |
| `INFO` | Normal service lifecycle and processing information. This is the recommended default for most deployments. |
| `WARNING` | Unexpected but recoverable conditions, such as configuration choices that are valid but risky. |
| `ERROR` | Processing or destination failures that require attention but do not necessarily stop the process. |
| `CRITICAL` | Severe failures where the process or deployment may be unable to continue safely. |

Payload logging is separate from the level. Keep `payload_logging` set to
`false` in production unless the deployment has explicitly approved payload
visibility. Message bodies may contain customer data, business data,
ciphertext, credentials, or regulated information.

## Redaction

`nats-sink show-effective-config` prints JSON with secret-looking values
redacted. It does not resolve or display destination passwords, NATS passwords,
tokens, private keys, or credential file contents.

```mermaid
flowchart LR
    File[config.json] --> Load[JSON loader]
    Env[Environment overrides] --> Load
    Load --> Validate[Pydantic validation]
    Validate --> Redact[Redacted JSON output]
    Validate --> Run[Runner construction]
```
