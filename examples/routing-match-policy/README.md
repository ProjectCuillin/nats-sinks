# Routing Match Policy Example

This example validates the generic route-match policy introduced for future
multi-sink routing. The policy evaluates normalized `NatsEnvelope` metadata and
returns logical target names. It does not yet execute multi-sink fan-out by
itself.

Validate the example:

```bash
nats-sink validate examples/routing-match-policy/config.json
```

The example contains two routes for the same subject family and labels:

- `nato_secret_sensor_audit` matches `mission.sensor.>` messages with
  `priority=urgent`, `classification=NATO SECRET`, labels `sensor` and `audit`,
  plus the non-secret `Nats-Sinks-Route: mission-audit` header. It selects the
  logical targets `oracle_secret` and `file_secret_audit`.
- `nato_unclass_sensor_audit` matches the same subject and label family with
  `classification=NATO UNCLASS`. It selects only `oracle_unclass`.

The actual Oracle and file sink instances behind those logical names can be
declared in the top-level `sinks` registry, as shown in
`examples/named-multi-sink/config.json`. Until fan-out delivery is enabled,
this example remains a validation and documentation fixture for the shared
route-selection model.
