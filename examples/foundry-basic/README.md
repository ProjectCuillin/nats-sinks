# Palantir Foundry Stream Example

This example shows the experimental Foundry sink configuration shape without
including a real Foundry endpoint, token, client identifier, or stream resource.

Validate the structure locally:

```bash
nats-sink validate examples/foundry-basic/config.json
```

Expected output:

```text
Configuration is valid.
Active sink: foundry
ACK policy: commit-then-acknowledge
```

Do not run this example as-is. Replace the placeholder endpoint and
`endpoint_allowed_hosts` value only in an ignored local copy after a Foundry
administrator has approved the stream push target and service identity.
