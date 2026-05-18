# Getting Started

This guide gets a local NATS stream and a local file sink configuration ready.
It is written for readers who may be new to NATS, JetStream, or sink
connectors.

NATS is the message broker. JetStream is the NATS feature that stores messages
and tracks whether consumers have acknowledged them. The file sink is the
simplest local destination because it does not require a database. The goal is
to publish one message to NATS, let `nats-sinks` write it to a durable
destination, and ACK the message only after that destination reports durable
success.

The examples assume Python `>=3.11`.

## Install

```bash
python -m pip install --upgrade pip
python -m pip install nats-sinks
```

For development:

```bash
python -m pip install -e ".[dev,oracle,docs]"
```

## Start NATS

Start a local NATS server with JetStream enabled. The `-js` flag turns on
JetStream storage. The `-m 8222` flag exposes a monitoring endpoint that is
useful during local development.

```bash
nats-server -js -m 8222
```

Create a stream and publish a test message:

```bash
nats stream add ORDERS --subjects "orders.*"
nats pub orders.created '{"order_id":"O-1001","amount":42.50}'
```

## Prepare The Destination

For the local file sink, choose an output directory. The tracked example uses
`.local/file-sink/events`, which is ignored by git. No credentials are required.

Oracle setup is documented separately in [Oracle Sink](oracle-sink.md). File
sink durability, duplicate behavior, and filesystem safety are documented in
[File Sink](file-sink.md).

## Configure

Runtime configuration is JSON-only:

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "sink": {
    "type": "file",
    "directory": ".local/file-sink/events",
    "filename_strategy": "stream_sequence",
    "duplicate_policy": "skip_existing",
    "payload_mode": "json_or_envelope"
  }
}
```

Do not put real credentials in config files. The file example does not require
secrets. Database sinks should use environment-backed fields such as
`password_env`.

## Validate And Run

```bash
nats-sink validate examples/file-basic/config.json
nats-sink show-effective-config examples/file-basic/config.json
nats-sink test-sink examples/file-basic/config.json
nats-sink run examples/file-basic/config.json
```

## What Success Means

Success is not just "the message was received." For this project, success means
the destination has completed the durable write and only then has JetStream
been ACKed. This is the central safety property of `nats-sinks`.

```mermaid
sequenceDiagram
    participant JS as JetStream
    participant R as nats-sinks
    participant S as Sink
    participant D as Destination

    JS->>R: Deliver batch
    R->>S: write_batch(envelopes)
    S->>D: Write records
    D-->>S: Durable success
    S-->>R: Return success
    R->>JS: ACK batch
```

The ACK is sent after durable sink success. If the process crashes after the
destination commit but before ACK, JetStream may redeliver the message. Use an
idempotent sink mode so that duplicate delivery is safe.
