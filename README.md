# nats-sinks

`nats-sinks` provides at-least-once delivery from JetStream to external destinations with commit-then-acknowledge processing and idempotent sink support.

The project repository is [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/). The current named contributor is Johan Louwers, reachable at [louwersj@gmail.com](mailto:louwersj@gmail.com).

## Overview

NATS is a lightweight messaging system used to move events between services.
JetStream is the persistence layer in NATS: it stores messages in streams and
delivers them to consumers. A sink is a consumer whose main job is to copy those
messages into another durable system, such as a database.

`nats-sinks` is a Python package for building outbound NATS JetStream sink consumers. It provides a reusable runtime that owns JetStream delivery semantics and delegates destination writes to sink implementations. The first production sink is Oracle Database.

The package is designed as a production-ready foundation rather than a demo script. It includes a typed public API, JSON configuration, a CLI, security-conscious defaults, tests, documentation, CI configuration, and packaging metadata suitable for publishing to PyPI.

Future sink modules are planned for:

- `nats_sinks.postgres`
- `nats_sinks.http`
- `nats_sinks.file`
- `nats_sinks.s3`

Those modules are not shipped as production sinks yet. The extension points are present so future sinks can implement the same contract without taking ownership of ACK behavior.

## Status

The current release is `0.1.1`.

Included today:

- Core JetStream pull-consumer runtime.
- Commit-then-acknowledge processing.
- Immutable `NatsEnvelope` abstraction.
- Explicit sink protocol and safe sink registry.
- Oracle sink with idempotent production modes.
- JSON configuration and redacted effective-config output.
- CLI command named `nats-sink`.
- Unit tests for ACK ordering, DLQ ordering, config loading, SQL generation, and Oracle mapping.
- Integration test placeholders isolated behind `integration` markers.
- MkDocs documentation, examples, GitHub Actions workflows, governance files, and security policy.

The package does not claim exactly-once delivery. It provides at-least-once
delivery with clear commit ordering and idempotent sink support. That means a
message may be delivered more than once, especially after failures, and sinks
must be configured so duplicate processing is safe.

## Architecture

```mermaid
flowchart LR
    Producer[Publisher] --> Stream[JetStream stream]
    Stream --> Consumer[Durable pull consumer]
    Consumer --> Runner[nats-sinks core runner]
    Runner --> Envelope[NatsEnvelope batch]
    Envelope --> Sink[sink.write_batch]
    Sink --> Commit[Durable destination commit]
    Commit --> Ack[JetStream ACK]

    Runner -. permanent failure .-> DLQ[DLQ publish]
    DLQ -. publish succeeds .-> Ack
```

The core rule is:

> Core owns delivery semantics. Sinks own destination writes.

Sinks never receive raw NATS messages. Sinks never ACK messages. The core runtime converts raw messages into `NatsEnvelope` instances, calls the sink, and ACKs only after durable success.

## Commit-Then-Acknowledge

The project invariant is:

> A JetStream message must only be acknowledged after all required durable side effects have completed successfully. ACK is the final confirmation of successful processing, never a prerequisite for processing.

Short slogan:

> Commit first. ACK last. Design for redelivery.

The normal processing sequence is:

```mermaid
sequenceDiagram
    participant JS as JetStream
    participant R as JetStreamSinkRunner
    participant S as Destination Sink
    participant D as Durable Destination

    JS->>R: Deliver message batch
    R->>R: Normalize into NatsEnvelope
    R->>S: write_batch(envelopes)
    S->>D: Write rows or records
    D-->>S: Commit succeeds
    S-->>R: Return success
    R->>JS: ACK messages
```

If the sink fails before durable commit, the core does not ACK. If commit succeeds but the process exits before ACK, JetStream may redeliver the message. That is expected and must be handled by idempotency.

## Installation

```bash
pip install nats-sinks
pip install "nats-sinks[oracle]"
pip install "nats-sinks[dev]"
pip install "nats-sinks[docs]"
pip install "nats-sinks[all]"
```

Python `>=3.11` is required.

## Quick Start

Start NATS with JetStream:

```bash
nats-server -js -m 8222
nats stream add ORDERS --subjects "orders.*"
nats pub orders.created '{"order_id":"O-1001","amount":42.50}'
```

Prepare the destination:

The first production destination is Oracle Database. Follow the table and
least-privilege setup in [Oracle Sink](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/oracle-sink.md), then run the sink
with the tracked example configuration.

Run the sink:

```bash
export ORACLE_PASSWORD=example
nats-sink validate examples/oracle-jetstream/config.json
nats-sink run examples/oracle-jetstream/config.json
```

## JSON Configuration

Runtime configuration is JSON-only. The package uses the standard-library JSON parser for application configuration.
The example below uses Oracle because it is the first production sink; future
sinks will use the same generic `nats`, `delivery`, `dead_letter`, `logging`,
and `metrics` sections with their own documented `sink` fields.

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*",
    "durable": true
  },
  "delivery": {
    "batch_size": 100,
    "batch_timeout_ms": 1000,
    "max_in_flight_batches": 1,
    "ack_policy": "after_sink_commit",
    "max_retries": 5,
    "retry_backoff_ms": 1000,
    "prefer_safe_duplication": true
  },
  "dead_letter": {
    "enabled": true,
    "subject": "orders.dlq",
    "include_payload": true,
    "include_headers": true,
    "include_error": true
  },
  "logging": {
    "level": "INFO",
    "payload_logging": false
  },
  "metrics": {
    "enabled": false,
    "namespace": "nats_sinks"
  },
  "sink": {
    "type": "oracle",
    "dsn": "localhost:1521/FREEPDB1",
    "user": "app_user",
    "password_env": "ORACLE_PASSWORD",
    "table": "NATS_SINK_EVENTS",
    "mode": "merge",
    "auto_create": false,
    "payload_mode": "json_or_envelope",
    "idempotency": {
      "strategy": "stream_sequence",
      "columns": ["STREAM_NAME", "STREAM_SEQUENCE"]
    }
  }
}
```

Secret values should come from the environment or a secret manager. Use
environment-backed fields such as `password_env` and `token_env` rather than
storing credentials in config files.

## Payload Bodies

NATS message bodies are bytes. The generic framework accepts bytes and does not
require JSON at the core boundary. Sinks that store data in JSON-capable
destinations can use the shared payload normalization contract for JSON,
encrypted text, plain text, and opaque bytes.

The default `payload_mode` is `json_or_envelope`:

- valid JSON is stored unchanged,
- non-JSON UTF-8 text is wrapped in a JSON envelope,
- non-text bytes are wrapped as base64 in the same JSON envelope.

For encrypted text streams where the ciphertext may or may not decrypt to JSON
later, use `payload_mode: "text_envelope"` to wrap every body as text and avoid
unnecessary JSON parse attempts.

```json
{
  "sink": {
    "type": "oracle",
    "table": "NATS_SINK_EVENTS",
    "mode": "merge",
    "payload_mode": "text_envelope"
  }
}
```

See [Sink Framework](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/sink-framework.md) and
[Oracle Sink](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/oracle-sink.md) for the JSON envelope shape and operational
guidance.

## Metadata Capture

`nats-sinks` captures a generic metadata JSON document for every message. This
is available to all current and future sinks through `NatsEnvelope`.

The metadata document preserves all message headers, known NATS-reserved
headers when present, unknown future `Nats-` headers, JetStream stream and
sequence metadata, optional reply subject, and timing fields. Optional headers
such as `Nats-Msg-Id` or `Nats-Expected-Stream` may be absent; that is normal
and does not cause a crash. Destination sinks can store this document directly
or map selected fields into destination-specific columns.

## NATS Connections

`nats-sinks` supports common NATS client authentication options through the
`nats` JSON section:

- token authentication with `token_env` or `token`,
- username/password authentication with `user` and `password_env` or `password`,
- server-side bcrypted username/password credentials using the same client-side
  `user` and `password_env` settings,
- TLS server verification with `tls_ca_file`, including private or self-signed
  NATS server CAs.

Do not embed credentials in `nats.url`; use environment-backed fields instead.
Advanced TLS certificate authentication policy, NKEY challenge authentication,
and decentralized JWT authentication/authorization are roadmap items. See
[NATS Connections And Authentication](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/nats-connections.md).

The broader comparison between NATS capabilities and the current project scope
is maintained in [NATS Feature Gap Analysis](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/nats-feature-gap-analysis.md).

## CLI

```bash
nats-sink --help
nats-sink validate examples/oracle-jetstream/config.json
nats-sink show-effective-config examples/oracle-jetstream/config.json
nats-sink test-sink examples/oracle-jetstream/config.json
nats-sink run examples/oracle-jetstream/config.json
```

The CLI:

- returns non-zero on validation or runtime errors,
- prints the active sink type,
- prints the commit-then-acknowledge ACK policy,
- renders effective configuration as redacted JSON,
- never prints resolved passwords.

## Python API

You can use `nats-sinks` directly from another Python project without shelling
out to the CLI. The recommended integration point is the public framework API:

```python
from nats_sinks import JetStreamSinkRunner
from nats_sinks.oracle import OracleSink

sink = OracleSink(
    dsn="localhost:1521/FREEPDB1",
    user="app_user",
    password_env="ORACLE_PASSWORD",
    table="NATS_SINK_EVENTS",
    mode="merge",
)

runner = JetStreamSinkRunner(
    nats_url="nats://localhost:4222",
    stream="ORDERS",
    consumer="orders-oracle-sink",
    subject="orders.*",
    sink=sink,
)

await runner.run()
```

You can also mount the Typer CLI into another Typer application:

```python
import typer
from nats_sinks.cli.main import app as nats_sink_cli

app = typer.Typer()
app.add_typer(nats_sink_cli, name="nats-sink")
```

See [Python Usage](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/python-usage.md) for embedded application patterns and
the tradeoff between using the public runtime API and importing CLI internals.

## Oracle Sink

Oracle-specific details are documented in [Oracle Sink](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/oracle-sink.md).
That page covers Oracle connection types, Autonomous Database, table DDL,
least-privilege users, idempotent write modes, subject-to-table routing,
payload storage, metadata columns, and Oracle-specific performance guidance.

The generic sink framework is documented separately in
[Sink Framework](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/sink-framework.md). That boundary is deliberate: future
backends can be added as new sink modules without changing the core
commit-then-acknowledge contract or making existing Oracle users change their
configuration.

## Failure Behavior

```mermaid
sequenceDiagram
    participant JS as JetStream
    participant R as Runner
    participant S as Sink
    participant DLQ as DLQ subject

    JS->>R: Deliver invalid message
    R->>S: write_batch(envelope)
    S-->>R: PermanentSinkError
    R->>DLQ: Publish diagnostic JSON
    DLQ-->>R: Publish acknowledged
    R->>JS: ACK original message
```

Important failure cases:

- destination write or commit fails: no ACK, message redelivers according to
  the JetStream consumer policy,
- destination commit succeeds and the process crashes before ACK: message may
  redeliver, so sink idempotency must handle the duplicate,
- payload is permanently invalid for the selected sink: message is published to
  DLQ when configured, then the original is ACKed only after DLQ publish
  succeeds,
- DLQ publish fails: original message is not ACKed.

## Security Notes

- Do not store secrets in repository files.
- Do not log payloads by default.
- Do not log passwords, tokens, private keys, NATS credentials, Oracle credentials, or full connection strings.
- SQL identifiers are allow-list validated.
- SQL values use bind variables.
- Unit tests must not make network calls.
- Integration tests are isolated behind markers.
- Use TLS and authenticated NATS connections in production.
- Use least-privilege destination credentials with access only to the required
  destination resources.

## Development

```bash
python -m pip install -e ".[dev,oracle,docs]"
ruff format --check .
ruff check .
mypy src
pytest
python -m build
twine check dist/*
```

Run only unit tests:

```bash
pytest -m "not integration"
```

Build documentation:

```bash
mkdocs build --strict
```

Manual live NATS connection testing is documented in
[NATS Connections And Authentication](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/nats-connections.md) and
[Testing](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/testing.md). The tracked helper script is
`scripts/nats-live-probe.py`; real CA files and credentials should stay under
ignored `.local/` paths.

The latest sanitized validation summary is maintained in
[Latest Test Report](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/test-report.md). That report is overwritten in place
for each new validation run and must not contain server addresses, usernames,
passwords, tokens, certificate contents, wallet material, connection strings,
or sensitive payloads.

To run `nats-sink` as a systemd service on Oracle Linux or Debian, see
[Service Deployment](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/service-deployment.md). The repository includes
example service files and installer scripts under `examples/systemd/` and
`scripts/`.

Release and PyPI publishing instructions are documented in
[Publishing Releases](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/publishing.md). That guide covers version updates,
tag pushes, GitHub release workflows, TestPyPI, PyPI trusted publishing, and
manual fallback commands.

## Repository Layout

```text
src/nats_sinks/core      Core runtime, config, envelope, runner, DLQ
src/nats_sinks/sinks     Sink protocols and registry
src/nats_sinks/oracle    Oracle sink implementation
src/nats_sinks/cli       CLI entry point
tests/unit               Deterministic unit tests
tests/integration        External-service test placeholders
docs                     MkDocs documentation
examples                 Local development examples
```

## Roadmap

Phase 1:

- Core runtime.
- Oracle sink.
- CLI.
- Documentation.
- Tests.
- PyPI-ready package.

Phase 2:

- Better metrics.
- More idempotency strategies.
- Postgres sink.
- HTTP sink.
- Docker image.
- Kubernetes examples.
- Multiple NATS seed URLs for clustered deployments.
- NATS reconnect tuning and connection event metrics.
- Least-privilege NATS permissions templates for sink users.
- Certified TLS certificate authentication guidance.
- Certified NKEY with challenge authentication support.
- Certified decentralized JWT authentication/authorization support.
- Explicit JetStream consumer creation and reconciliation.
- Configurable consumer `AckWait`, `MaxDeliver`, `BackOff`, and `MaxAckPending`.
- Optional `AckSync` / double-ACK and `InProgress` support.
- JetStream advisory consumption for operational events.

Phase 3:

- Plugin discovery.
- Sink certification tests.
- Helm chart.
- Advanced observability.
- WebSocket connection support.
- Push and ordered consumer evaluation where compatible with project semantics.
- Stream management helpers and documentation.
- Server monitoring endpoint integration.
- Future sink certification tests.

Not planned unless scope changes:

- `AckNone`, early ACK, and `AckAll` behavior that weakens commit-then-ack.
- General-purpose Core NATS pub/sub, queue group, request/reply, or services
  framework support.
- JetStream Key/Value and Object Store APIs unless a future sink needs them.

See [NATS Feature Gap Analysis](https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/nats-feature-gap-analysis.md) for the
detailed comparison.

## License

Apache-2.0. See `LICENSE`.
