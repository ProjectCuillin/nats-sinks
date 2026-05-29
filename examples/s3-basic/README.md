# S3-Compatible Object Sink Example

This example validates the first-party S3-compatible object sink without
opening a network connection. It is safe for local configuration checks because
`nats-sink validate` only parses and validates the JSON configuration.

```bash
nats-sink validate examples/s3-basic/config.json
```

Expected output:

```text
Configuration is valid.
Active sink: s3
ACK policy: commit-then-acknowledge
```

Running or test-starting the sink requires the optional SDK dependency and a
reviewed S3-compatible endpoint, bucket, and credential source:

```bash
python -m pip install "nats-sinks[s3]"
```

Do not store access keys in this example file. Use environment variables,
instance or workload identity, or a protected SDK profile.
