# Local Docker Test Stack

This example builds a local `nats-sinks` container image and runs it beside a
NATS JetStream container. It is intended for developer smoke testing, not as a
production deployment pattern.

The stack writes messages from the `orders.*` subject family to a mounted file
sink directory under `.local/docker-file-sink`.

## Run The Smoke Test

From the repository root:

```bash
python scripts/run-docker-local-smoke.py
```

The script performs the full local flow:

1. Builds the local `nats-sinks` image.
2. Starts a temporary NATS JetStream container.
3. Creates an `ORDERS` stream.
4. Publishes test messages to `orders.created`.
5. Starts the `nats-sink` container.
6. Waits until the file sink has persisted the expected files.
7. Stops the temporary Compose project unless `--keep-running` is used.

Use a different message count when you want to exercise batch flushing with a
non-default number of events:

```bash
python scripts/run-docker-local-smoke.py --message-count 13
```

Keep the containers running for manual inspection:

```bash
python scripts/run-docker-local-smoke.py --keep-running --keep-output
```

## Manual Compose Use

Build the image and start only NATS:

```bash
docker compose -f examples/docker-local/compose.json build nats-sink
docker compose -f examples/docker-local/compose.json up -d nats
```

Create the stream and publish messages using your preferred NATS CLI, then
start the sink:

```bash
docker compose -f examples/docker-local/compose.json up -d nats-sink
```

Stop the stack:

```bash
docker compose -f examples/docker-local/compose.json down
```

## Security Notes

The image runs as a non-root user and expects configuration and output paths to
be mounted explicitly. It deliberately avoids embedding credentials, wallets,
certificates, or environment-specific connection details.

Production image hardening, signing, SBOM publication, vulnerability scanning,
and public registry publishing are tracked as follow-up backlog items.
