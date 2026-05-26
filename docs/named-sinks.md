# Named Sinks And Routing

Named sinks let one configuration file declare several destination instances
with stable operator-facing names. They are the configuration foundation for
multi-sink routing and future fan-out delivery. A route can select
`oracle_secret`, `oracle_unclass`, `file_audit`, or any other declared name
without embedding Oracle connection settings, file paths, or credentials inside
the route itself.

The feature is intentionally backward compatible. Existing deployments can keep
using a single top-level `sink` object. The new top-level `sinks` object is a
registry of additional named sink instances. The route-match policy references
those names in `routing.routes[].targets`.

Named sinks do not by themselves change ACK behavior. The active top-level
`sink` is still the sink used by `nats-sink run` today. The named registry
exists so configuration, validation, redaction, health checks, route reporting,
and future fan-out execution all use one stable naming model.

## Configuration Shape

```json
{
  "sink": {
    "type": "file",
    "directory": ".local/active-file-sink/events",
    "fsync": false
  },
  "sinks": {
    "oracle_secret": {
      "type": "oracle",
      "dsn": "tcps://adb.example.invalid/secret",
      "user": "app_secret",
      "password_env": "ORACLE_SECRET_PASSWORD",
      "table": "NATS_SECRET_EVENTS"
    },
    "file_audit": {
      "type": "file",
      "directory": ".local/file-audit/events",
      "fsync": false
    }
  },
  "routing": {
    "enabled": true,
    "routes": [
      {
        "name": "nato_secret_sensor_audit",
        "match": {
          "subject": "mission.sensor.>",
          "classification": ["NATO SECRET"],
          "labels_all": ["sensor", "audit"]
        },
        "targets": ["oracle_secret", "file_audit"]
      }
    ]
  }
}
```

The sections have different responsibilities:

- `sink` is the active runtime sink for the current single-sink runner.
- `sinks` is the named registry used by route validation, redacted config
  output, `test-sink --sink-name`, and future fan-out execution.
- `routing` contains match rules and target names only. It should not contain
  destination credentials, Oracle table definitions, file paths, or driver
  settings.

## Name Rules

Named sink names use the same grammar as route target names:

- start with a letter,
- contain only letters, digits, `.`, `_`, `:`, or `-`,
- stay within 128 characters,
- remain unique in the JSON object.

The loader rejects duplicate JSON object keys before validation. This avoids
ambiguous configuration where one sink definition silently replaces another.

## Validation

`nats-sink validate` validates all of the following without opening NATS or a
destination connection:

- the active top-level `sink`,
- every named sink under `sinks`,
- every route target reference when `sinks` is configured,
- sink-specific fields such as file sink `directory`, Oracle `dsn`, Oracle
  `user`, and Oracle password source,
- redaction-safe route target reporting.

Example:

```bash
nats-sink validate examples/named-multi-sink/config.json
```

Example output:

```text
Configuration is valid.
Active sink: file
ACK policy: commit-then-acknowledge
Named sinks: file_audit (file), oracle_secret (oracle), oracle_unclass (oracle)
Route target references:
  - nato_secret_sensor_audit: oracle_secret (required), file_audit (optional, minimum_wait_ms=250, timeout_ms=1000)
  - nato_unclass_sensor_audit: oracle_unclass (required)
```

If a route references an unknown named sink, validation fails before runtime:

```text
Configuration error: routing targets reference unknown named sink(s): oracle_missing
```

## Redacted Effective Config

Named sink definitions are included in redacted effective configuration. Secret
fields are hidden while operator-facing names remain visible.

```bash
nats-sink show-effective-config examples/named-multi-sink/config.json
```

Example excerpt:

```json
{
  "sinks": {
    "oracle_secret": {
      "type": "oracle",
      "dsn": "tcps://adb.example.invalid/secret",
      "user": "app_secret",
      "password_env": "********",
      "table": "NATS_SECRET_EVENTS"
    },
    "file_audit": {
      "type": "file",
      "directory": ".local/named-multi-sink/audit",
      "fsync": false
    }
  }
}
```

Route target names such as `oracle_secret` are not treated as secrets. The name
may describe an operational lane, classification lane, or storage role, but it
does not contain credentials.

## Health Checking Named Sinks

The default `test-sink` command still checks the active top-level `sink`.

```bash
nats-sink test-sink examples/file-basic/config.json
```

To check one named sink:

```bash
nats-sink test-sink examples/named-multi-sink/config.json --sink-name file_audit
```

Example output:

```text
Named sink selected: file_audit (file)
ACK policy: commit-then-acknowledge
Sink test succeeded for file_audit.
Sink test succeeded.
```

To check every named sink:

```bash
nats-sink test-sink examples/named-multi-sink/config.json --all-named-sinks
```

Use this carefully for database sinks because it opens destination connections.
For local file sinks it is a useful dependency-free smoke test.

## Two Oracle Backends

This pattern writes different routed event families to different Oracle
Database backends. It is useful when classification, coalition releasability,
tenant, mission, or operational boundary decisions require separate database
accounts or separate OCI Autonomous Database instances.

```json
{
  "sinks": {
    "oracle_secret": {
      "type": "oracle",
      "dsn": "tcps://adb.example.invalid/secret",
      "user": "app_secret",
      "password_env": "ORACLE_SECRET_PASSWORD",
      "table": "NATS_SECRET_EVENTS"
    },
    "oracle_unclass": {
      "type": "oracle",
      "dsn": "tcps://adb.example.invalid/unclass",
      "user": "app_unclass",
      "password_env": "ORACLE_UNCLASS_PASSWORD",
      "table": "NATS_UNCLASS_EVENTS"
    }
  },
  "routing": {
    "enabled": true,
    "routes": [
      {
        "name": "nato_secret_sensor_audit",
        "match": {
          "subject": "mission.sensor.>",
          "priority": ["urgent"],
          "classification": ["NATO SECRET"],
          "labels_all": ["sensor", "audit"],
          "headers": [
            {
              "name": "Nats-Sinks-Route",
              "values": ["mission-audit"]
            }
          ]
        },
        "targets": [
          "oracle_secret",
          {
            "sink": "file_audit",
            "required": false,
            "minimum_wait_ms": 250,
            "timeout_ms": 1000
          }
        ]
      },
      {
        "name": "nato_unclass_sensor_audit",
        "match": {
          "subject": "mission.sensor.>",
          "priority": ["urgent"],
          "classification": ["NATO UNCLASS"],
          "labels_all": ["sensor", "audit"]
        },
        "targets": ["oracle_unclass"]
      }
    ]
  }
}
```

## Two Tables In One Oracle Backend

Two named sink instances can point at the same Oracle backend with different
tables. This keeps route policy independent from Oracle table names and lets
operators review each storage lane as a separate target.

```json
{
  "sinks": {
    "oracle_secret_events": {
      "type": "oracle",
      "dsn": "tcps://adb.example.invalid/secret",
      "user": "app_secret",
      "password_env": "ORACLE_SECRET_PASSWORD",
      "table": "NATS_SECRET_EVENTS"
    },
    "oracle_secret_audit": {
      "type": "oracle",
      "dsn": "tcps://adb.example.invalid/secret",
      "user": "app_secret",
      "password_env": "ORACLE_SECRET_PASSWORD",
      "table": "NATS_SECRET_AUDIT_EVENTS"
    }
  }
}
```

## Oracle Plus File

Oracle can hold the durable relational record while a file sink creates a local
handoff, audit copy, or replay aid.

```json
{
  "sinks": {
    "oracle_primary": {
      "type": "oracle",
      "dsn": "tcps://adb.example.invalid/primary",
      "user": "app_events",
      "password_env": "ORACLE_EVENTS_PASSWORD",
      "table": "NATS_EVENTS"
    },
    "file_audit": {
      "type": "file",
      "directory": "/var/lib/nats-sinks/audit",
      "duplicate_policy": "skip_existing",
      "compression": "gzip"
    }
  }
}
```

## Two File Destinations

Two file destinations can separate local audit records from replay buffers or
from cross-domain handoff preparation.

```json
{
  "sinks": {
    "file_audit": {
      "type": "file",
      "directory": "/var/lib/nats-sinks/audit",
      "filename_strategy": "stream_sequence",
      "duplicate_policy": "skip_existing",
      "fsync": true
    },
    "file_replay_buffer": {
      "type": "file",
      "directory": "/var/lib/nats-sinks/replay-buffer",
      "filename_strategy": "message_id",
      "duplicate_policy": "skip_existing",
      "compression": "gzip",
      "fsync": true
    }
  }
}
```

## Delivery Boundary

Until multi-sink fan-out execution is enabled, `nats-sink run` writes to the
single active `sink`. The named registry is still valuable now because it lets
teams review and validate multi-destination policy ahead of fan-out delivery.

When fan-out execution is enabled in a future release, ACK behavior must remain
explicit. Required targets will need durable success before ACK. Optional
targets can define bounded wait policy, such as `minimum_wait_ms` and
`timeout_ms`, so operators can decide which destinations are part of the ACK
gate and which destinations are best-effort within an approved time window.
