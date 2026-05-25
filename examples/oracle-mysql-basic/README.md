# Oracle MySQL Basic Example

This example shows the JSON configuration shape for the first-party Oracle
MySQL sink. It is intended for local development and operator familiarization;
replace the host, database, user, table, and TLS settings with values approved
for your environment before running it against a real database.

The example uses `password_env` so the Oracle MySQL password stays outside the
repository and outside shell history.

## Install The Optional Dependency

```bash
python -m pip install "nats-sinks[mysql]"
```

## Validate Configuration

```bash
export ORACLE_MYSQL_PASSWORD=example
nats-sink validate examples/oracle-mysql-basic/config.json
```

## Test The Sink

`test-sink` opens a connection, performs a health check, and, when
`auto_create` is enabled, verifies the configured table can be created.

```bash
export ORACLE_MYSQL_PASSWORD=example
nats-sink test-sink examples/oracle-mysql-basic/config.json
```

## Run With NATS

Start a local NATS server with JetStream and an `ORDERS` stream, then run:

```bash
nats-sink run examples/oracle-mysql-basic/config.json
```

The sink writes `orders.*` messages to `NATS_SINK_EVENTS` and routes
`orders.priority.*` messages to `NATS_SINK_PRIORITY_EVENTS`. Both tables use
the standard Oracle MySQL row shape and the same payload normalization contract
as the Oracle Database and file sinks.

## Local Container E2E

For repeatable local certification without a shared database, use the
short-lived Oracle MySQL test container:

```bash
python scripts/run-mysql-sink-e2e.py
```

The script generates random credentials, uses a loopback-only port, and removes
the container, Docker volume, and generated secret files by default.
