# Local File Sink Example

This example writes JetStream messages to local JSON files. It is useful for
development, demos, and local durability tests because it does not require a
database.

The example writes under `.local/file-sink/events`, which is ignored by git.
That keeps generated message files out of the repository.

## Validate Configuration

```bash
nats-sink validate examples/file-basic/config.json
```

## Test The Sink

```bash
nats-sink test-sink examples/file-basic/config.json
```

The command creates the output directory if it is missing and performs a small
local write health check. It does not connect to NATS.

## Run With Local NATS

Start a local NATS server with JetStream:

```bash
nats-server -js -m 8222
nats stream add ORDERS --subjects "orders.*"
```

Run the sink:

```bash
nats-sink run examples/file-basic/config.json
```

Publish a test message:

```bash
nats pub orders.created '{"order_id":"O-1001","amount":42.50}'
```

The sink writes one JSON file per message using the JetStream stream sequence
as the default idempotency key.
