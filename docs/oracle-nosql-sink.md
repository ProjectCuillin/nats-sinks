# Oracle NoSQL Database Sink

The Oracle NoSQL Database sink is a first-party, experimental sink for storing
one complete normalized nats-sinks event as a JSON value field in one Oracle
NoSQL Database table row.

The sink follows the same core delivery rule as the other built-in sinks:

```text
Commit first. ACK last. Design for redelivery.
```

The sink is intentionally conservative. It does not let messages choose table
names, field names, SDK modules, authentication modes, or generated DDL. It
opens one configured table, derives deterministic keys from approved
idempotency metadata, writes complete JSON-compatible event values, and returns
success only after the Oracle NoSQL Python SDK reports an unambiguous put
success or a configured duplicate-safe conditional put result.

## Status

The connector is built in and available as `sink.type: "oracle_nosql"`, but it
is marked experimental in the connector registry. Local unit tests and sink
certification tests prove the nats-sinks integration contract with a fake SDK
adapter. Live Oracle NoSQL Database certification is gated and depends on a
local Oracle NoSQL Database KVLite or Cloud Simulator target.

Use the sink as an ACK-gated durable custody target only after reviewing and
testing the Oracle NoSQL Database deployment mode, replication, consistency,
proxy, backup, identity, and restore posture. Until then, it is often better
to use the sink as an optional fan-out target next to a required Oracle
Database, Oracle MySQL, file, or encrypted edge spool target.

The production runtime does not use a mock, stub, or local fake client. The
runtime path imports the official Oracle NoSQL Python SDK lazily when the sink
starts and builds a fixed SDK handle configuration from validated nats-sinks
configuration. Fake clients are used only by deterministic unit and
certification tests so normal test runs do not require network access.

## Production-Readiness Matrix

The connector-wide registry status remains `experimental` and
`production_ready: false` until every deployment mode selected for production
recommendation has accepted live evidence. The current certification surface is
explicit:

| Deployment mode | Auth mode | Current status | Evidence |
| --- | --- | --- | --- |
| `kvstore` | `store_access_token` | Locally certified against the maintained short-lived KVLite container e2e path. Production deployments still require operator review of the actual store, proxy, replication, backup, restore, and access-control posture. | Unit tests, sink certification helpers, and `python scripts/run-oracle-nosql-sink-e2e.py`. |
| `cloudsim` | `cloudsim` | Experimental. The SDK provider construction is covered by deterministic tests, but the standard release gate does not currently start an Oracle NoSQL Cloud Simulator target. | Unit tests and the live-certification runbook below. |
| `cloud` | `oci_config_file` | Experimental. The SDK signature-provider construction, OCI profile/config-file option handling, optional passphrase environment lookup, and default compartment application are covered by deterministic tests. Live Oracle NoSQL Database Cloud Service certification is environment-gated. | Unit tests and the live-certification runbook below. |
| `cloud` | `instance_principal` | Experimental. The SDK instance-principal provider construction is covered by deterministic tests, but live certification requires an approved OCI runtime identity. | Unit tests and the live-certification runbook below. |
| Any other deployment/auth combination | n/a | Unsupported and rejected during configuration validation. | Configuration validation tests. |

Do not change the connector registry to production-ready while any mode that
operators are expected to use for production remains untested or undocumented.
If only one mode is certified, document that mode explicitly and leave the
connector-wide metadata conservative.

## Install

The base package does not install the Oracle NoSQL Python SDK. Install the
optional extra when the sink will connect to Oracle NoSQL Database:

```bash
python -m pip install "nats-sinks[oracle-nosql]"
```

Expected package metadata includes the optional dependency:

```text
borneo>=5,<6
```

For Oracle NoSQL Database Cloud Service, the SDK may also use OCI identity
configuration. Keep credentials in approved platform locations and avoid
putting private keys, passphrases, or profile contents into nats-sinks JSON.

## Minimal Configuration

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "EVENTS",
    "consumer": "oracle-nosql-sink",
    "subject": "events.>"
  },
  "sink": {
    "type": "oracle_nosql",
    "endpoint": "127.0.0.1:8080",
    "deployment_mode": "kvstore",
    "table_name": "nats_sinks_events"
  }
}
```

Validate the configuration without opening Oracle NoSQL Database:

```bash
nats-sink validate examples/oracle-nosql-basic/config.json
```

Expected output:

```text
Configuration is valid.
Active sink: oracle_nosql
ACK policy: commit-then-acknowledge
```

## Full Configuration Example

```json
{
  "sink": {
    "type": "oracle_nosql",
    "endpoint": "127.0.0.1:8080",
    "deployment_mode": "kvstore",
    "auth_mode": "store_access_token",
    "table_name": "nats_sinks_events",
    "key_field": "sink_key",
    "value_field": "event_json",
    "stored_at_field": "stored_at_epoch_ns",
    "namespace": null,
    "compartment_id": null,
    "cloudsim_tenant_id": "cloudsim",
    "oci_config_file": null,
    "oci_profile": "DEFAULT",
    "oci_private_key_passphrase_env": null,
    "key_strategy": "stream_sequence",
    "key_prefix": "mission-demo",
    "duplicate_policy": "skip_existing",
    "payload_mode": "json_or_envelope",
    "auto_create": false,
    "read_units": 10,
    "write_units": 10,
    "storage_gb": 1,
    "table_timeout_ms": 50000,
    "table_poll_interval_ms": 3000,
    "max_key_bytes": 512,
    "max_value_bytes": 1048576,
    "request_timeout_seconds": 10,
    "durability": "operator_confirmed"
  }
}
```

| Field | Required | Default | Valid values | Description |
| --- | --- | --- | --- | --- |
| `type` | yes | none | `oracle_nosql` | Selects the Oracle NoSQL Database sink. |
| `endpoint` | no | `127.0.0.1:8080` | `host:port` or `http(s)://host:port` without userinfo, path, query, or fragment. | SDK endpoint for KVStore proxy, Cloud Simulator, or cloud service. |
| `deployment_mode` | no | `kvstore` | `kvstore`, `cloudsim`, or `cloud` | Selects the SDK authorization provider style. |
| `auth_mode` | no | mode-specific | `store_access_token`, `cloudsim`, `oci_config_file`, or `instance_principal` | Must match the deployment mode. |
| `table_name` | no | `nats_sinks_events` | One or two dot-separated identifiers. | Table receiving event rows. |
| `key_field` | no | `sink_key` | Identifier. | Primary-key field populated by nats-sinks. |
| `value_field` | no | `event_json` | Identifier. | JSON field containing the full normalized event value. |
| `stored_at_field` | no | `stored_at_epoch_ns` | Identifier. | Long field containing the sink storage timestamp in epoch nanoseconds. |
| `namespace` | no | none | Bounded text. | Optional SDK default namespace when supported by the target. |
| `compartment_id` | no | none | Bounded text. | Optional cloud compartment reference applied to the SDK handle configuration when supported by the Oracle NoSQL Python SDK. |
| `cloudsim_tenant_id` | no | `cloudsim` | Bounded text. | Non-secret Cloud Simulator namespace token. |
| `oci_config_file` | no | none | Local path string. | Optional OCI SDK config-file path. The file contents are never printed by nats-sinks. |
| `oci_profile` | no | `DEFAULT` | Bounded profile name. | OCI SDK profile name for cloud deployments. |
| `oci_private_key_passphrase_env` | no | none | Environment variable name. | Optional env-var name used to read a private-key passphrase at runtime. |
| `key_strategy` | no | `idempotency_key` | `idempotency_key`, `stream_sequence`, `message_id`, or `payload_sha256` | Determines the deterministic row key. |
| `key_prefix` | no | none | Letters, numbers, dots, underscores, colons, or hyphens; up to 128 characters. | Optional namespace prefix prepended to every generated key. |
| `duplicate_policy` | no | `skip_existing` | `skip_existing`, `replace`, or `fail_existing` | Behavior when the key already exists. |
| `payload_mode` | no | `json_or_envelope` | Core payload storage mode. | Valid JSON is stored as JSON; text or binary payloads can be wrapped in the standard envelope. |
| `auto_create` | no | `false` | `true` or `false` | When true, the sink creates the configured table using generated safe DDL. |
| `read_units` | no | `10` | `1` to `50000` | Cloud table limit used when `auto_create` is true in cloud mode. |
| `write_units` | no | `10` | `1` to `50000` | Cloud table limit used when `auto_create` is true in cloud mode. |
| `storage_gb` | no | `1` | `1` to `1024` | Cloud table storage limit used when `auto_create` is true in cloud mode. |
| `table_timeout_ms` | no | `50000` | `1000` to `600000` | Table creation wait timeout. |
| `table_poll_interval_ms` | no | `3000` | `100` to `60000` | Table creation wait polling interval. |
| `max_key_bytes` | no | `512` | `64` to `4096` | Maximum UTF-8 key size after prefixing. |
| `max_value_bytes` | no | `1048576` | `1` to `16777216` | Maximum JSON row/value size before the SDK write is attempted. |
| `request_timeout_seconds` | no | `10` | `>0` to `300` | Per startup and write timeout used by nats-sinks. |
| `durability` | no | `operator_confirmed` | `operator_confirmed` | Records that the operator owns the Oracle NoSQL durability review. |

## Table Shape

The generated table model is a narrow key/value row:

```sql
CREATE TABLE IF NOT EXISTS nats_sinks_events (
  sink_key STRING,
  event_json JSON,
  stored_at_epoch_ns LONG,
  PRIMARY KEY(sink_key)
)
```

The generated DDL is built only from already-validated identifiers. Do not use
message metadata or payload fields to choose table or field names.

## Stored Value Shape

The configured `value_field` contains a single JSON-compatible object. It
preserves the normalized event, payload, headers, standard NATS metadata,
priority, classification, labels, mission metadata, data-centric security
labels, and custody metadata.

Example value:

```json
{
  "schema": "nats_sinks.oracle_nosql.event.v1",
  "schema_version": 1,
  "subject": "events.created",
  "stream": "EVENTS",
  "stream_sequence": 42,
  "consumer": "oracle-nosql-sink",
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
    "consumer": "oracle-nosql-sink"
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

| Policy | Oracle NoSQL operation | Behavior |
| --- | --- | --- |
| `skip_existing` | conditional put with `IF_ABSENT` | Existing key is treated as a successful prior write. This is the default redelivery-safe behavior. |
| `replace` | unconditional put | Existing row is overwritten. Use only when replacing committed values is acceptable. |
| `fail_existing` | conditional put with `IF_ABSENT` | Existing key raises a permanent sink error. Use only for flows where duplicate redelivery should go to DLQ or operator review. |

## Fan-Out Example

Oracle NoSQL Database can be a required or optional child sink in active
fan-out. This example requires Oracle Database before ACK and gives the Oracle
NoSQL side copy a bounded grace window:

```json
{
  "sink": {
    "type": "fanout"
  },
  "sinks": {
    "oracle_primary": {
      "type": "oracle",
      "dsn": "oracle-primary-service",
      "user": "app_user",
      "password_env": "ORACLE_PRIMARY_PASSWORD",
      "table": "NATS_SINK_EVENTS"
    },
    "nosql_read_model": {
      "type": "oracle_nosql",
      "endpoint": "127.0.0.1:8080",
      "deployment_mode": "kvstore",
      "table_name": "nats_sinks_events",
      "duplicate_policy": "skip_existing"
    }
  },
  "routing": {
    "enabled": true,
    "routes": [
      {
        "name": "events_to_oracle_and_nosql",
        "match": {
          "subject": "events.>"
        },
        "targets": [
          "oracle_primary",
          {
            "sink": "nosql_read_model",
            "required": false
          }
        ]
      }
    ]
  }
}
```

Because `nosql_read_model` is optional and the named sink type is known, the
loader applies the Oracle NoSQL optional ACK-gate defaults:

```text
minimum_wait_ms=1000
timeout_ms=5000
```

## Local Container Verification

Normal unit tests use fake SDK clients and make no network calls. Local
container-backed testing uses the Oracle NoSQL Database KVLite test backend
documented in [Oracle NoSQL Database Test Backend](oracle-nosql-test-container.md).

Run a backend-only smoke test:

```bash
python scripts/run-oracle-nosql-container-smoke.py
```

Expected output:

```text
Oracle NoSQL Database container smoke test passed with one verified JSON key/value entry.
```

Run the sink e2e test against a fresh short-lived KVLite container:

```bash
python scripts/run-oracle-nosql-sink-e2e.py
```

Expected output:

```text
Oracle NoSQL sink container e2e test passed.
```

Both helpers are local-only, bind the Oracle NoSQL proxy to `127.0.0.1`, use
fake event data, and remove the container by default. Do not commit container
layers, runtime database files, generated logs, or preserved debug artifacts.

## Live Certification Runbook

Live certification is intentionally gated. Run it only from an approved local
environment with fake event data, an operator-approved test table, and
sanitized evidence capture. Do not paste private endpoints, tenancy names,
profile contents, credential paths, tokens, passphrases, payloads, or table
contents into issue comments or release notes.

For a local KVLite or Oracle NoSQL Database proxy target, prefer the maintained
container e2e helper:

```bash
python scripts/run-oracle-nosql-sink-e2e.py --timeout-seconds 300
```

Expected output:

```text
Oracle NoSQL sink container e2e test passed.
```

For an operator-managed Cloud Simulator or Oracle NoSQL Database Cloud Service
test target, use the environment-gated integration test. Replace the example
values with local, sanitized test-only values and keep any secret-bearing OCI
configuration outside the repository:

```bash
NATS_SINKS_ORACLE_NOSQL_INTEGRATION=1 \
NATS_SINKS_ORACLE_NOSQL_MODE=cloud \
NATS_SINKS_ORACLE_NOSQL_ENDPOINT=https://nosql.example.invalid \
NATS_SINKS_ORACLE_NOSQL_TABLE=nats_sinks_certification_events \
NATS_SINKS_ORACLE_NOSQL_AUTO_CREATE=1 \
python -m pytest tests/integration/test_oracle_nosql_sink_e2e.py -q
```

Expected sanitized result:

```text
1 passed
```

For Cloud Simulator certification, set
`NATS_SINKS_ORACLE_NOSQL_MODE=cloudsim` and use the simulator endpoint and
test table approved for the local run. For OCI config-file or
instance-principal cloud runs, verify the runtime identity and least-privilege
policy outside the repository before executing the test. The gated integration
test also accepts these optional environment variables when the live target
needs them:

| Environment variable | Purpose |
| --- | --- |
| `NATS_SINKS_ORACLE_NOSQL_AUTH_MODE` | Optional explicit auth mode such as `oci_config_file` or `instance_principal`. |
| `NATS_SINKS_ORACLE_NOSQL_NAMESPACE` | Optional SDK default namespace. |
| `NATS_SINKS_ORACLE_NOSQL_COMPARTMENT_ID` | Optional SDK default compartment reference. |
| `NATS_SINKS_ORACLE_NOSQL_CLOUDSIM_TENANT_ID` | Optional non-secret Cloud Simulator tenant token. |
| `NATS_SINKS_ORACLE_NOSQL_OCI_CONFIG_FILE` | Optional local OCI config-file path. Do not commit the file or path into evidence. |
| `NATS_SINKS_ORACLE_NOSQL_OCI_PROFILE` | Optional OCI config profile name. |
| `NATS_SINKS_ORACLE_NOSQL_OCI_PRIVATE_KEY_PASSPHRASE_ENV` | Optional name of the environment variable that contains the private-key passphrase. This is the variable name only, not the passphrase value. |

Evidence is acceptable for release notes only when it says which deployment
mode was tested, which command shape was run, whether the connector metadata
changed, and whether any deployment modes remain experimental. Keep raw logs
and live service details out of committed documentation.

## Limitations

- The connector is experimental and not yet live-certified against every Oracle
  NoSQL Database deployment mode.
- `auto_create` uses generated safe DDL for the default key/value table shape.
  Custom table DDL is intentionally not accepted from JSON config.
- Cloud least-privilege policy examples must be reviewed against the exact OCI
  tenancy and compartment model before production use.
- Payload encryption protects payload bytes only. Subjects, stream metadata,
  message IDs, priority, classification, labels, table names, and row keys
  remain metadata.
