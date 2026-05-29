# Palantir Gotham Object Example

This example shows the experimental Gotham sink configuration shape without
including a real Gotham endpoint, token, client identifier, object type, or
property type from a customer environment.

Validate the example with:

```bash
nats-sink validate examples/gotham-basic/config.json
```

Expected output:

```text
Configuration is valid.
Active sink: gotham
ACK policy: commit-then-acknowledge
```

Use a real Gotham base URL, `endpoint_allowed_hosts`, object type, and property
type mapping only in an ignored local copy after an approved non-production
Gotham environment and least-privileged service identity have been prepared.
