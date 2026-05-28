# Multi-Sink Routing End-To-End Flow

The multi-sink routing end-to-end flow is a deterministic release-readiness
check for fan-out configuration. It exercises the production `FanoutSink` and
the production route selector, but it replaces live destinations with local
file-backed probe sinks by default. This keeps the test safe for ordinary
developer machines while still proving the routing and ACK-gate behavior that
matters before live backend tests are attempted.

The flow is intentionally payload-safe. It records message IDs, subject
families, priority, classification, labels, route headers, stream sequence
numbers, selected sink names, duplicate attempts, and evidence counts. It does
not store payload bodies, credentials, endpoint URLs from live systems, table
contents, certificate material, wallet material, or local paths in the JSON
report.

## What It Proves

The reduced flow validates one configuration file with `nats-sink validate`
semantics, then runs a synthetic route matrix through the real fan-out sink.
It covers:

- subject matching;
- priority matching;
- classification matching;
- `labels_all`, `labels_any`, and `labels_none`;
- approved non-secret routing headers;
- static configuration gates such as `Nats-Sinks-Flow`;
- one message routed to one sink;
- one message routed to multiple sink types;
- no-route handling with `ignore` and `reject`;
- optional sink timeout behavior;
- required sink failure after partial success;
- duplicate redelivery safety in the local probes.

The current route matrix uses these logical sink targets:

| Target | Sink Type | ACK Role |
| --- | --- | --- |
| `oracle_primary` | Oracle Database | Required |
| `mysql_audit` | Oracle MySQL Database | Required |
| `file_audit` | File | Optional bounded wait |
| `coherence_read_model` | Oracle Coherence Community Edition | Optional bounded wait |
| `oracle_unclass` | Oracle Database | Required |

The reduced flow is not a replacement for live Oracle Database, Oracle MySQL
Database, File, or Oracle Coherence Community Edition sink tests. It proves the
shared routing layer and ACK-gate behavior before those live destination tests
run.

## Configuration

The tracked example is:

```bash
examples/multi-sink-routing-e2e/config.json
```

Each individual sink is configured in the top-level `sinks` registry:

```json
{
  "sinks": {
    "oracle_primary": {
      "type": "oracle",
      "dsn": "oracle-primary-service",
      "user": "app_user",
      "password_env": "NATS_SINKS_ORACLE_PRIMARY_PASSWORD",
      "table": "MISSION_SECRET_EVENTS"
    },
    "mysql_audit": {
      "type": "mysql",
      "host": "127.0.0.1",
      "port": 3306,
      "database": "mission_audit",
      "user": "app_user",
      "password_env": "NATS_SINKS_ORACLE_MYSQL_AUDIT_PASSWORD",
      "table": "mission_audit_events"
    },
    "file_audit": {
      "type": "file",
      "directory": "var/lib/nats-sinks/mission-audit",
      "filename_strategy": "stream_sequence",
      "duplicate_policy": "skip_existing",
      "fsync": true
    },
    "coherence_read_model": {
      "type": "coherence",
      "address": "127.0.0.1:1408",
      "cache_name": "mission-routing-read-model",
      "key_strategy": "message_id"
    }
  }
}
```

The routing policy decides which named sinks receive each message:

```json
{
  "routing": {
    "enabled": true,
    "mode": "first",
    "no_match": "ignore",
    "routes": [
      {
        "name": "secret_sensor_multi_sink",
        "match": {
          "subject": "mission.sensor.>",
          "priority": "urgent",
          "classification": "NATO SECRET",
          "labels_all": ["sensor", "audit"],
          "labels_any": ["edge", "gateway"],
          "labels_none": ["training"],
          "headers": [
            {"name": "Nats-Sinks-Route", "values": ["mission-audit"]},
            {"name": "Nats-Sinks-Flow", "values": ["multi-sink-routing-e2e"]}
          ]
        },
        "targets": [
          "oracle_primary",
          "mysql_audit",
          {
            "sink": "file_audit",
            "required": false,
            "minimum_wait_ms": 10,
            "timeout_ms": 50
          },
          {
            "sink": "coherence_read_model",
            "required": false,
            "minimum_wait_ms": 10,
            "timeout_ms": 50
          }
        ]
      }
    ]
  }
}
```

Required targets must commit before the original JetStream message can be
ACKed. Optional targets are attempted, but they can be bounded by
`minimum_wait_ms` and `timeout_ms` so a side-copy target does not block ACK
forever.

## Run The Reduced Flow

Run the deterministic local flow:

```bash
python scripts/run-multi-sink-routing-e2e.py --mode reduced
```

Write a sanitized JSON report:

```bash
python scripts/run-multi-sink-routing-e2e.py \
  --mode reduced \
  --output .local/multi-sink-routing-e2e/report.json
```

The output is deterministic and pipe-friendly:

```json
{
  "actual_by_sink": {
    "coherence_read_model": ["MSG-SECRET-1", "MSG-TASKING-1"],
    "file_audit": ["MSG-SECRET-1"],
    "mysql_audit": ["MSG-SECRET-1"],
    "oracle_primary": ["MSG-SECRET-1"],
    "oracle_unclass": ["MSG-UNCLASS-1"]
  },
  "config_validated": true,
  "duplicate_attempts_by_sink": {
    "coherence_read_model": 2,
    "file_audit": 1,
    "mysql_audit": 1,
    "oracle_primary": 1,
    "oracle_unclass": 1
  },
  "mode": "reduced",
  "no_route_message_ids": ["MSG-NO-ROUTE-1", "MSG-TRAINING-1"],
  "optional_timeout_observed": true,
  "reject_no_route_observed": true,
  "required_failure_blocked_ack": true,
  "schema_version": 1
}
```

The reduced flow intentionally triggers optional-timeout, required-failure, and
no-route safety paths. Depending on logging configuration, stderr can include
short fan-out safety messages while stdout remains the JSON report.

## Validate The Config Separately

The example can be validated without running the probe flow:

```bash
nats-sink validate examples/multi-sink-routing-e2e/config.json
```

Expected summary:

```text
Configuration is valid.
Active sink: fanout
ACK policy: commit-then-acknowledge
Named sinks: coherence_read_model (coherence), file_audit (file), mysql_audit (mysql), oracle_primary (oracle), oracle_unclass (oracle)
Route target references:
  - secret_sensor_multi_sink: oracle_primary (required), mysql_audit (required), file_audit (optional, minimum_wait_ms=10, timeout_ms=50), coherence_read_model (optional, minimum_wait_ms=10, timeout_ms=50)
  - unclass_sensor_oracle: oracle_unclass (required)
  - tasking_coherence_read_model: coherence_read_model (optional, minimum_wait_ms=10, timeout_ms=50)
```

## Release Checks

The reduced flow is part of `scripts/check-sinks.sh`:

```bash
scripts/check-sinks.sh
```

That script runs the focused unit tests, validates the tracked config, and
writes a sanitized local report to:

```bash
.local/check-sinks/multi-sink-routing-report.json
```

The report is local release evidence only and should not be copied into public
issue comments without review.

## Live Backend Testing

Live backend testing remains separate and explicitly gated because it requires
local infrastructure and credentials. Run the reduced flow first, then combine
it with the destination-specific test scripts that match the backends under
review:

```bash
python scripts/run-multi-sink-routing-e2e.py --mode reduced
scripts/run-oracle-e2e.sh
python scripts/run-mysql-sink-e2e.py
python scripts/run-coherence-sink-e2e.py
pytest tests/integration/test_file_sink_e2e.py
```

This layering keeps routing certification deterministic while still allowing a
maintainer to validate real durable writes against local Oracle Database,
Oracle MySQL Database, file, and Oracle Coherence Community Edition targets
when those systems are available.
