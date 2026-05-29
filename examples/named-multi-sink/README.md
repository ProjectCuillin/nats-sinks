# Named Multi-Sink Configuration Example

This example shows how to declare several named sink instances in one JSON
configuration file. It keeps a normal active file sink for backwards-compatible
single-sink operation while also preparing the same named targets that
`sink.type: "fanout"` can dispatch to.

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

The top-level `sink` remains the active sink used by `nats-sink run` in this
example. To run fan-out, change the active sink to `{"type": "fanout"}` or use
the compact inline form in `examples/fanout/config.json`.
