# Named Multi-Sink Configuration Example

This example shows how to declare several named sink instances in one JSON
configuration file while preserving the current single active runtime sink.

Validate the configuration:

```bash
nats-sink validate examples/named-multi-sink/config.json
```

Check the local file audit target without opening Oracle connections:

```bash
nats-sink test-sink examples/named-multi-sink/config.json --sink-name file_audit
```

The example declares:

- `oracle_secret`, an Oracle Database target for NATO SECRET sensor audit
  events.
- `oracle_unclass`, an Oracle Database target for NATO UNCLASS sensor audit
  events.
- `file_audit`, a local file target that can be used for an audit copy or
  local handoff.

The `routing` section references only those names. It does not contain Oracle
connection strings, Oracle users, password environment-variable names, or file
paths. Destination-specific settings live under `sinks`.

The top-level `sink` remains the active sink used by `nats-sink run` until
multi-sink fan-out delivery is enabled in a future release. The named registry
is already validated and reported by the CLI so teams can prepare route policy
and destination definitions ahead of that delivery step.
