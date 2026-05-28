# Oracle Coherence Community Edition Sink

The Oracle Coherence Community Edition sink is a first-party, experimental
sink for storing one complete normalized nats-sinks event as the JSON value of
one Oracle Coherence key/value entry.

It is useful when a deployment needs a low-latency Oracle Coherence Community
Edition data grid view of events while preserving the same core delivery rule
used by the other built-in sinks:

```text
Commit first. ACK last. Design for redelivery.
```

The sink is intentionally conservative. It does not treat Coherence as a
relational database, does not build dynamic cache names from messages, and does
not let message metadata choose arbitrary modules, serializers, or methods. It
opens one configured cache or map, derives deterministic keys from approved
idempotency metadata, writes complete JSON-compatible values, and returns
success only after the Coherence client operation completes.

## Status

The connector is built in and available as `sink.type: "coherence"`, but it is
marked experimental in the connector registry. The local unit tests and the
container-backed e2e flow prove the nats-sinks integration contract. They do
not prove that a given production Oracle Coherence cluster has persistence,
backup, recovery, replication, authorization, TLS, or operational durability
configured correctly.

Operators should use the sink as an ACK-gated durable custody target only after
they have reviewed and tested the Oracle Coherence Community Edition cluster
policy that backs the configured cache or map. Until then, it is often better
to use the sink as an optional fan-out target next to a required Oracle
Database, Oracle MySQL, file, or encrypted edge spool target.

## Install

The base package does not install the Oracle Coherence Python client. Install
the optional extra when the sink will connect to Oracle Coherence:

```bash
python -m pip install "nats-sinks[coherence]"
```

Expected package metadata includes the optional dependency:

```text
coherence-client>=2,<3
```

## Minimal Configuration

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "EVENTS",
    "consumer": "coherence-sink",
    "subject": "events.>"
  },
  "sink": {
    "type": "coherence",
    "address": "127.0.0.1:1408",
    "cache_name": "nats_sinks_events"
  }
}
```

Validate the configuration without opening Oracle Coherence:

```bash
nats-sink validate examples/oracle-coherence-basic/config.json
```

Expected output:

```text
Configuration is valid.
Active sink: coherence
ACK policy: commit-then-acknowledge
```

## Full Configuration Example

```json
{
  "sink": {
    "type": "coherence",
    "address": "127.0.0.1:1408",
    "scope": "",
    "cache_name": "mission_event_cache",
    "storage": "cache",
    "serializer": "json",
    "key_strategy": "stream_sequence",
    "key_prefix": "mission-demo",
    "duplicate_policy": "skip_existing",
    "payload_mode": "json_or_envelope",
    "ttl_seconds": 86400,
    "max_key_bytes": 512,
    "max_value_bytes": 1048576,
    "request_timeout_seconds": 10,
    "ready_timeout_seconds": 30,
    "session_disconnect_seconds": 30,
    "durability": "operator_confirmed"
  }
}
```

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `type` | yes | none | `coherence` | Selects the Oracle Coherence Community Edition sink. |
| `address` | no | `127.0.0.1:1408` | `host:port` without scheme or userinfo. | Coherence gRPC endpoint. Credentials and URLs are intentionally not accepted in this field. |
| `scope` | no | empty string | Plain bounded text. | Optional Coherence scope. |
| `cache_name` | no | `nats_sinks_events` | Letters, numbers, dots, underscores, or hyphens; up to 128 characters. | Named cache or map receiving event values. |
| `storage` | no | `cache` | `cache` or `map` | `cache` uses `Session.get_cache(...)`; `map` uses `Session.get_map(...)`. |
| `serializer` | no | `json` | `json` | Coherence client serializer format. Other serializers are rejected. |
| `key_strategy` | no | `idempotency_key` | `idempotency_key`, `stream_sequence`, `message_id`, `payload_sha256` | Determines the deterministic K/V key. |
| `key_prefix` | no | none | Letters, numbers, dots, underscores, colons, or hyphens; up to 128 characters. | Optional namespace prefix prepended to every generated key. |
| `duplicate_policy` | no | `skip_existing` | `skip_existing`, `replace`, `fail_existing` | Behavior when the key already exists. |
| `payload_mode` | no | `json_or_envelope` | Core payload storage mode. | Valid JSON is stored as JSON; text or binary payloads can be wrapped in the standard envelope. |
| `ttl_seconds` | no | none | `1` to `31536000`; cache only. | Optional cache TTL. Rejected when `storage` is `map`. |
| `max_key_bytes` | no | `512` | `64` to `4096` | Maximum UTF-8 key size after prefixing. |
| `max_value_bytes` | no | `1048576` | `1` to `16777216` | Maximum JSON value size before the client write is attempted. |
| `request_timeout_seconds` | no | `10` | `>0` to `300` | Per startup and write timeout used by nats-sinks. |
| `ready_timeout_seconds` | no | `30` | `0` to `300` | Passed to the Coherence client session options. |
| `session_disconnect_seconds` | no | `30` | `0` to `300` | Passed to the Coherence client session options. |
| `durability` | no | `operator_confirmed` | `operator_confirmed` | Records that the operator owns the Coherence durability review. |

## Stored Value Shape

The value is a single JSON-compatible object. It preserves the normalized
event, payload, headers, standard NATS metadata, priority, classification,
labels, mission metadata, data-centric security labels, and custody metadata.

Example value:

```json
{
  "schema": "nats_sinks.coherence.event.v1",
  "schema_version": 1,
  "subject": "events.created",
  "stream": "EVENTS",
  "stream_sequence": 42,
  "consumer": "coherence-sink",
  "consumer_sequence": 7,
  "message_id": "event-42",
  "priority": "high",
  "classification": "NATO UNCLASSIFIED",
  "labels": "sensor;audit",
  "labels_list": ["sensor", "audit"],
  "payload": {
    "event_id": "event-42",
    "status": "created"
  },
  "payload_info": {
    "original_format": "json",
    "wrapped": false,
    "sha256": "example-redacted",
    "size_bytes": 42
  },
  "headers": {
    "Nats-Msg-Id": "event-42"
  },
  "mission_metadata": {
    "phase": "find"
  },
  "security_labels": {
    "classification": "NATO UNCLASSIFIED"
  },
  "custody": null,
  "metadata": {
    "stream": "EVENTS",
    "consumer": "coherence-sink"
  }
}
```

Payload bytes are not logged by default. The example uses fake values and a
redacted hash placeholder.

## Key Strategies

| Strategy | Key form | Notes |
| --- | --- | --- |
| `idempotency_key` | `stream-sequence:<stream>:<sequence>`, then message ID, then payload hash fallback. | Recommended default. Uses the core envelope idempotency helper. |
| `stream_sequence` | `stream-sequence:<stream>:<sequence>` | Fails closed when stream metadata is missing. |
| `message_id` | `message-id:<message_id>` | Fails closed when no message ID is available. |
| `payload_sha256` | `payload-sha256:<subject>:<sha256>` | Available for simple replay scenarios, but avoid it when payload encryption is enabled because ciphertext is intentionally non-deterministic. |

Do not use priority, classification, labels, or mission metadata as keys. They
are operational metadata, not duplicate-detection identifiers.

## Duplicate Policies

| Policy | Coherence operation | Behavior |
| --- | --- | --- |
| `skip_existing` | `put_if_absent` | Existing key is treated as a successful prior write. This is the default redelivery-safe behavior. |
| `replace` | `put` | Existing value is overwritten. Use only when replacing committed values is acceptable. |
| `fail_existing` | `put_if_absent` | Existing key raises a permanent sink error. Use only for flows where duplicate redelivery should go to DLQ or operator review. |

## Fan-Out Example

The Coherence sink can be used as a required or optional child sink in active
fan-out. This example requires Oracle Database before ACK and gives the
Coherence side copy a bounded grace window:

```json
{
  "sink": {
    "type": "fanout"
  },
  "sinks": {
    "oracle_primary": {
      "type": "oracle",
      "dsn": "tcps://database.example.invalid/service",
      "user": "app_user",
      "password_env": "NATS_SINKS_ORACLE_PASSWORD",
      "table": "NATS_SINK_EVENTS"
    },
    "coherence_read_model": {
      "type": "coherence",
      "address": "127.0.0.1:1408",
      "cache_name": "mission_read_model",
      "duplicate_policy": "skip_existing"
    }
  },
  "routing": {
    "enabled": true,
    "routes": [
      {
        "name": "events_to_oracle_and_coherence",
        "match": {
          "subject": "events.>"
        },
        "targets": [
          "oracle_primary",
          {
            "sink": "coherence_read_model",
            "required": false
          }
        ]
      }
    ]
  }
}
```

Because `coherence_read_model` is optional and the named sink type is known,
the loader applies the Coherence optional ACK-gate defaults:

```text
minimum_wait_ms=1000
timeout_ms=5000
```

## Local E2E Test

The repository includes a local Oracle Coherence Community Edition test
backend at [Oracle Coherence CE Test Backend](oracle-coherence-test-container.md).
Run the sink e2e flow from an isolated environment that has Docker and the
optional Coherence Python client:

```bash
python scripts/run-coherence-sink-e2e.py
```

Expected output:

```text
.                                                                        [100%]
1 passed
Oracle Coherence sink e2e test passed.
```

The script builds the local test image, starts a short-lived Coherence
container on a random loopback port, runs
`tests/integration/test_coherence_sink_e2e.py`, reads the stored JSON value back
through the Coherence client, removes the test key, and removes the container
by default.

When Docker is not available, the integration test stays skipped unless
explicitly enabled:

```bash
python -m pytest tests/integration/test_coherence_sink_e2e.py -q
```

Expected output:

```text
s                                                                        [100%]
1 skipped
```

## Security Notes

- Treat cache names, key prefixes, TTLs, serializer mode, duplicate policy,
  and value size limits as configuration trust boundaries.
- Keep Coherence security, TLS, persistence, backups, and cluster
  authorization in the Coherence deployment. The sink does not grant or
  manage those controls.
- Do not put credentials, endpoints with userinfo, payloads, message IDs,
  classification values, labels, or subjects into public issue comments,
  evidence, or metrics labels.
- Keep the Coherence Python client behind the optional `coherence` extra so
  the base package stays small and dependency review stays explicit.
- Use `skip_existing` or another reviewed idempotency strategy before making
  the sink a required ACK-gated destination.
