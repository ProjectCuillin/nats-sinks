# Live NATS Probe Example

This directory contains templates for testing a real NATS connection without
committing secrets.

Use this only with non-production systems or with explicit operator approval.
The probe can subscribe to a subject and can optionally publish a test message
after the subscription is active.

For mission or defence lab environments, make sure the chosen subject is
approved for testing and that the probe message cannot be confused with a real
operational event. The script prints payload sizes by default and avoids
printing payload contents unless explicitly requested.

## Prepare Local Ignored Files

Create an ignored local directory:

```bash
mkdir -p .local/nats-live
chmod 700 .local/nats-live
```

Write your CA certificate to:

```text
.local/nats-live/ca.crt
```

Create an ignored env file:

```bash
cat > .local/nats-live/nats-sink.env <<'EOF'
NATS_PASSWORD=replace-with-test-password
EOF
chmod 600 .local/nats-live/ca.crt .local/nats-live/nats-sink.env
```

Do not commit `.local/`. The repository `.gitignore` excludes it.

## Subscribe Without Publishing

```bash
python scripts/nats-live-probe.py \
  --server tls://nats.example.com:4222 \
  --user example_user \
  --password-env NATS_PASSWORD \
  --env-file .local/nats-live/nats-sink.env \
  --ca-file .local/nats-live/ca.crt \
  --subject example.test.subject
```

The probe prints connection status, subscription status, and the size of any
received payload. It does not print payload contents unless `--print-payload` is
set.

## Publish And Receive A Test Message

Use this only when publishing to the subject is safe:

```bash
python scripts/nats-live-probe.py \
  --server tls://nats.example.com:4222 \
  --user example_user \
  --password-env NATS_PASSWORD \
  --env-file .local/nats-live/nats-sink.env \
  --ca-file .local/nats-live/ca.crt \
  --subject example.test.subject \
  --publish \
  --message '{"probe":"nats-sinks","kind":"live-test"}'
```

Expected success output includes:

```text
connected: server=tls://nats.example.com:4222 subject=example.test.subject
subscribed: waiting up to 20s for one message
published: subject=example.test.subject payload_bytes=41
received: subject=example.test.subject payload_bytes=41
closed
```

## Token Authentication

For token authentication, store `NATS_TOKEN` in the ignored env file and use:

```bash
python scripts/nats-live-probe.py \
  --server tls://nats.example.com:4222 \
  --auth-mode token \
  --token-env NATS_TOKEN \
  --env-file .local/nats-live/nats-sink.env \
  --ca-file .local/nats-live/ca.crt \
  --subject example.test.subject
```

For credentials-file, NKEY seed-file, and TLS client certificate deployments,
use the JSON configuration examples in
[NATS Connections And Authentication](https://nats-sinks.readthedocs.io/en/latest/nats-connections/)
and the gated integration test documented in
[Testing](https://nats-sinks.readthedocs.io/en/latest/testing/). Keep identity
files under ignored local directories or runtime secret mounts.
