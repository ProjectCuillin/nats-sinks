# Oracle NoSQL Database Test Backend

The Oracle NoSQL Database test backend is a local-only KVLite container used
to certify the experimental Oracle NoSQL Database sink without relying on a
manual database setup. It is not a production deployment, benchmark target, or
durability claim.

The helper uses Oracle's documented Community Edition KVLite image from GitHub
Container Registry:

```text
ghcr.io/oracle/nosql:latest-ce
```

Oracle documents this container path for developer testing with a KVLite store
and HTTP proxy. The nats-sinks helper wraps that official image instead of
building a custom image. That means there is no repository-local Dockerfile for
this backend and no local claim about the image's base operating-system layer.
If a future custom image becomes necessary, it must prefer Oracle Linux 9 slim
where technically feasible and document any exception.

## Security Boundary

The default helper runs a non-secure KVLite proxy because the official Oracle
quick-start path for local developer containers exposes a plain HTTP proxy. This
mode is allowed only for local fake data.

The helper keeps the test boundary narrow:

- It binds the proxy to `127.0.0.1` on a random local port.
- It generates a collision-resistant short-lived container name.
- It does not use host networking, privileged mode, or Docker socket mounts.
- It drops Linux capabilities and enables `no-new-privileges`.
- It does not mount persistent host storage by default.
- It removes the container by default.
- It prints sanitized pass/fail output and does not print payloads, secrets, or
  live service details.

Use `--preserve-artifacts` only for local debugging. Do not include preserved
container names, logs, or runtime paths in issue comments or release evidence.

## Install Test Dependency

The helper needs the optional Oracle NoSQL Python SDK because the smoke test
writes and reads a JSON row through the same SDK family used by the sink:

```bash
python -m pip install -e ".[oracle-nosql]"
```

Expected package metadata includes:

```text
borneo>=5,<6
```

## Smoke Test

Run the backend smoke test:

```bash
python scripts/run-oracle-nosql-container-smoke.py
```

Expected successful output:

```text
Oracle NoSQL Database container smoke test passed with one verified JSON key/value entry.
```

The smoke test performs the following actions:

1. Verifies Docker is available.
2. Pulls `ghcr.io/oracle/nosql:latest-ce` unless it is already available.
3. Starts a short-lived KVLite container with `KV_PROXY_PORT=8080`.
4. Waits for the loopback proxy port.
5. Creates or verifies a local table named `nats_sinks_nosql_smoke_events`.
6. Writes one complete fake normalized event JSON object.
7. Reads the row back by key and compares the full JSON value.
8. Removes the container unless `--preserve-artifacts` is set.

The generated table shape is intentionally the same narrow key/value shape used
by the Oracle NoSQL sink:

```sql
CREATE TABLE IF NOT EXISTS nats_sinks_nosql_smoke_events (
  sink_key STRING,
  event_json JSON,
  stored_at_epoch_ns LONG,
  PRIMARY KEY(sink_key)
)
```

## Sink End-To-End Test

After the smoke test is available, issue #149 can be verified against the local
container with:

```bash
python scripts/run-oracle-nosql-sink-e2e.py
```

Expected successful output:

```text
Oracle NoSQL sink container e2e test passed.
```

The e2e helper starts the same KVLite container, sets the live integration
environment for `tests/integration/test_oracle_nosql_sink_e2e.py`, enables
`auto_create`, writes the same normalized envelope twice, and relies on the
sink's `skip_existing` duplicate policy to prove redelivery-safe duplicate
handling.

## Custom Image Reference

Use `--image-ref` to test a different Oracle NoSQL Database image reference:

```bash
python scripts/run-oracle-nosql-container-smoke.py \
  --image-ref ghcr.io/oracle/nosql:latest-ce
```

The image reference is validated for basic command-safety properties before it
is passed to Docker. Keep alternate references public and non-sensitive in
documentation and issue comments.

## Debug Preservation

Preserve the backend only when you need to inspect local behavior:

```bash
python scripts/run-oracle-nosql-container-smoke.py --preserve-artifacts
```

When preservation is enabled, remove the container manually after debugging:

```bash
docker rm -f <local-container-name>
```

Do not commit container layers, runtime store files, generated logs, or local
debug output.

## Troubleshooting

If the SDK is missing, install the optional extra:

```text
The optional borneo package is required for this smoke test.
```

If Docker is not running, start the local Docker-compatible runtime and retry:

```text
Oracle NoSQL Database container smoke test failed: Command failed with exit code ...
```

If the image cannot be pulled, verify local network access to GitHub Container
Registry and retry. The helper intentionally does not fall back to third-party
images.
