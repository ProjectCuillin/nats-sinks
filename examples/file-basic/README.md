# Local File Sink Example

This example writes JetStream messages to local JSON files. It is useful for
development, demos, and local durability tests because it does not require a
database.

It is also a useful first step for controlled operational prototypes: teams can
inspect the exact payload and metadata record that would later be written to a
database or object store, including priority, classification, labels, timing,
and JetStream sequence information.

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

## Optional Gzip Compression

The example keeps compression disabled so generated files are easy to inspect.
To write gzip-compressed JSON files, set:

```json
{
  "sink": {
    "compression": "gzip",
    "compression_level": 6
  }
}
```

When gzip is enabled and no custom extension is configured, the file sink uses
`.json.gz` filenames and still writes one durable file per message.
