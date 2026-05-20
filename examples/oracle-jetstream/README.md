# Oracle JetStream Example

This example demonstrates the Oracle sink as an operational event store. It is
useful when you want to validate idempotent database writes, metadata columns,
and commit-before-ACK behavior before connecting a real mission or enterprise
stream.

Start services:

```bash
docker compose -f examples/docker-compose.nats.json up
docker compose -f examples/docker-compose.oracle.json up
```

Prepare NATS:

```bash
nats stream add ORDERS --subjects "orders.*"
```

Run the sink:

```bash
export ORACLE_PASSWORD=example
nats-sink validate examples/oracle-jetstream/config.json
nats-sink run examples/oracle-jetstream/config.json
```

Run the routed-table example:

```bash
export ORACLE_PASSWORD=example
nats-sink validate examples/oracle-jetstream/config-routed.json
nats-sink run examples/oracle-jetstream/config-routed.json
```

Publish a test event:

```bash
python examples/oracle-jetstream/producer.py
```
