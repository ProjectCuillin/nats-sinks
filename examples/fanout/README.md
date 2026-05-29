# Fan-Out Sink Example

This example shows one active `fanout` sink dispatching the same NATS subject
family to multiple concrete child sinks. It is intentionally safe to validate
without opening NATS, Oracle Database, or the filesystem:

```bash
nats-sink validate examples/fanout/config.json
```

The configuration contains two routes:

- urgent `NATO SECRET` sensor audit events are written to required
  `oracle_secret` custody storage and an optional `file_audit` side copy;
- urgent `NATO UNCLASS` sensor audit events are written only to required
  `oracle_unclass` storage.

Required targets must complete before the runner may ACK the original
JetStream message. Optional targets receive a bounded wait window and can time
out without blocking ACK. Fan-out is at-least-once across destinations, not an
atomic distributed transaction, so every production child sink should use its
normal idempotency controls.
