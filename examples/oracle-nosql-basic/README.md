# Oracle NoSQL Database Basic Example

This example validates the experimental Oracle NoSQL Database sink configuration
without opening a live Oracle NoSQL Database handle.

```bash
nats-sink validate examples/oracle-nosql-basic/config.json
```

Expected output:

```text
Configuration is valid.
Active sink: oracle_nosql
ACK policy: commit-then-acknowledge
```

Install the optional SDK dependency before running against a local KVLite,
Cloud Simulator, or Oracle NoSQL Database proxy target:

```bash
python -m pip install "nats-sinks[oracle-nosql]"
```
