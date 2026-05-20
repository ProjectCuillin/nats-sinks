# Payload Encryption Example

This example shows how to enable generic core payload encryption. The setting
is top-level because encryption happens before any sink receives the message.
The same `encryption` block works with the file sink, Oracle sink, and future
sinks that accept normal `NatsEnvelope` batches.

The example uses an environment variable for key material:

```bash
export NATS_SINKS_PAYLOAD_KEY_B64="$(python -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())')"
```

The generated value is for local testing only. Do not commit real keys.

This example is relevant for sensitive operational streams where the payload
body should be unreadable in the destination while subjects, sequence numbers,
priority, classification, and labels remain available for routing and audit.

Validate the local file example:

```bash
nats-sink validate examples/payload-encryption/file-config.json
nats-sink test-sink examples/payload-encryption/file-config.json
```

The file sink writes the encrypted payload envelope into each output file's
`payload` field. Metadata remains clear so routing, idempotency, and
troubleshooting continue to work.

## Subject-Specific Example

`file-subject-rules-config.json` shows the same core encryption feature with
ordered subject rules. It encrypts subjects matching `secure.>` and leaves
subjects matching `public.>` unchanged. Any subject with no matching rule also
remains unchanged because the top-level `enabled` value is `false`.

```bash
export NATS_SINKS_SECURE_PAYLOAD_KEY_B64="$(python -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())')"
nats-sink validate examples/payload-encryption/file-subject-rules-config.json
nats-sink test-sink examples/payload-encryption/file-subject-rules-config.json
```

Use this shape when a single runner consumes a broad subject such as `>` or
`orders.>` but only selected subject families should be encrypted before the
file, Oracle, or future sink receives the envelope.
