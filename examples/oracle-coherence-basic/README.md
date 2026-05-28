# Oracle Coherence Community Edition Sink Example

This example validates the first-party experimental Oracle Coherence Community
Edition sink configuration without connecting to Oracle Coherence.

```bash
nats-sink validate examples/oracle-coherence-basic/config.json
```

Expected output:

```text
Configuration is valid.
Active sink: coherence
ACK policy: commit-then-acknowledge
```

For a local container-backed write/read test, install the optional Coherence
client in an isolated environment and run:

```bash
python scripts/run-coherence-sink-e2e.py
```

The e2e script starts a short-lived Oracle Coherence Community Edition test
container, writes one complete fake event JSON value through the sink, reads it
back through the Coherence client, and removes the container by default.
