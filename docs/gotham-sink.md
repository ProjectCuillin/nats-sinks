# Palantir Gotham Sink

The Palantir Gotham sink is an experimental first-party connector for durable
event handoff into Gotham RevDB object creation. It maps normalized NATS
envelopes into configured Gotham object properties while preserving the normal
delivery contract: the core runner ACKs only after `GothamSink.write_batch(...)`
returns success.

This connector is not production-certified. It has local fake-client contract
tests, but it must be validated against an approved non-production Gotham
environment, approved object model, approved service identity, and exact Gotham
API surface before any production-ready claim is made.

## Research Decision

Issue #151 evaluated the public Gotham documentation and selected RevDB object
creation as the first supported target. Public Gotham documentation describes a
REST API using OAuth2 and JSON requests and responses, with current support for
RevDB, an object database backed by a dynamic ontology. Public documentation
also describes object creation through
`POST /api/gotham/v1/objects/types/{objectType}`, which returns a `primaryKey`
for the created object.

Preview surfaces such as Inbox messages and geotemporal observations were not
selected for this increment. They are useful to evaluate later, but they are
preview APIs and have narrower workflow meaning than a controlled object-model
handoff.

Sources:

- [Gotham API introduction](https://www.palantir.com/docs/gotham/api)
- [Gotham create object API](https://www.palantir.com/docs/gotham/api/revdb-resources/objects/create-object/)
- [Gotham OAuth2 clients](https://www.palantir.com/docs/gotham/platform-security-third-party/writing-oauth2-clients)
- [Gotham third-party application registration](https://www.palantir.com/docs/gotham/platform-security-third-party/register-3pa)
- [Gotham write observations preview API](https://www.palantir.com/docs/gotham/api/v1/geotime-resources/observations/write-observations/)
- [Gotham send inbox messages preview API](https://www.palantir.com/docs/gotham/api/v1/inbox-resources/messages/inbox-send-messages/)

## Scope

| Field | Value |
| --- | --- |
| Sink type | `gotham` |
| Supported target | Gotham RevDB object creation |
| Public import | `nats_sinks.gotham.GothamSink` |
| Config type | `nats_sinks.gotham.GothamSinkConfig` |
| Client protocol | `nats_sinks.gotham.GothamObjectClient` |
| Certification status | Local fake-client contract tests only |
| Production readiness | `false` |

The sink does not implement target workbench, target boards, HPTL management,
inbox delivery, observation writing, map mutation, tactical automation,
targeting, fire-control, weapons release, rules-of-engagement evaluation, or
autonomous decision-making.

## Minimal Configuration

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "MISSION_EVENTS",
    "consumer": "gotham-sink",
    "subject": "mission.events.*"
  },
  "sink": {
    "type": "gotham",
    "gotham_base_url": "https://gotham.example.invalid",
    "object_type": "com.example.object.event",
    "external_id_property_type": "com.example.property.externalId",
    "subject_property_type": "com.example.property.subject",
    "payload_property_type": "com.example.property.payload",
    "payload_info_property_type": "com.example.property.payloadInfo",
    "bearer_token_env": "GOTHAM_TOKEN",
    "endpoint_allowed_hosts": ["gotham.example.invalid"]
  }
}
```

Validate the example:

```bash
nats-sink validate examples/gotham-basic/config.json
```

Expected output:

```text
Configuration is valid.
Active sink: gotham
ACK policy: commit-then-acknowledge
```

## OAuth2 Client Credentials

Gotham service-style integrations can use OAuth2 client credentials after a
third-party application has been registered and granted the required resource
permissions by an administrator.

```json
{
  "type": "gotham",
  "gotham_base_url": "https://gotham.example.invalid",
  "object_type": "com.example.object.event",
  "external_id_property_type": "com.example.property.externalId",
  "subject_property_type": "com.example.property.subject",
  "payload_property_type": "com.example.property.payload",
  "auth_mode": "oauth2_client_credentials",
  "oauth2_token_url": "https://gotham.example.invalid/multipass/api/oauth2/token",
  "oauth2_client_id_env": "GOTHAM_CLIENT_ID",
  "oauth2_client_secret_env": "GOTHAM_CLIENT_SECRET",
  "endpoint_allowed_hosts": ["gotham.example.invalid"]
}
```

The environment variable names are public configuration. The secret values are
read only at write time and must never be committed, logged, or included in
issue comments.

## Object Mapping

Each NATS envelope becomes one Gotham object-create request:

```json
{
  "title": "nats-sinks event stream-sequence:SENSOR_EVENTS:42",
  "properties": [
    {
      "propertyType": "com.example.property.externalId",
      "value": "stream-sequence:SENSOR_EVENTS:42"
    },
    {
      "propertyType": "com.example.property.subject",
      "value": "mission.sensor.event"
    },
    {
      "propertyType": "com.example.property.payload",
      "value": {
        "sensor_id": "S-1",
        "status": "ok"
      }
    }
  ],
  "validationMode": "STRICT",
  "security": {
    "portionMarkings": ["SENSITIVE"]
  }
}
```

The actual object type and property type names must come from the target Gotham
ontology. The sample names are placeholders.

Optional property mappings can persist:

- payload information such as format, wrapping, SHA-256, and payload size;
- normalized NATS metadata;
- priority, classification, and labels;
- mission metadata;
- security labels;
- custody metadata.

Unset optional property mappings are skipped so strict Gotham ontology models
do not receive unexpected properties.

## Idempotency

`external_id_property_type` is required. It stores a deterministic external ID
derived from one of these strategies:

| Strategy | Meaning |
| --- | --- |
| `idempotency_key` | Uses the core envelope idempotency key. This is the default. |
| `stream_sequence` | Uses the NATS stream name and stream sequence. |
| `message_id` | Uses the NATS message ID and fails closed if absent. |
| `payload_sha256` | Uses the subject and payload digest. |

The public Gotham create-object API does not by itself prove global uniqueness
for that property. Live deployments should configure the Gotham object model,
representative properties, ontology constraints, or downstream resolution rules
so redelivery can be recognized safely. `treat_conflict_as_duplicate` is off by
default and should be enabled only after live Gotham behavior proves that HTTP
409 means an idempotent duplicate for the configured external ID model.

## Limits

| Field | Default | Meaning |
| --- | --- | --- |
| `batch_size` | `25` | Maximum prepared objects passed to the client per sink chunk. |
| `max_object_bytes` | `262144` | Maximum JSON size for one object-create request. |
| `max_batch_bytes` | `4194304` | Maximum aggregate JSON size for one prepared chunk. |
| `max_response_bytes` | `65536` | Maximum response body read from Gotham. |
| `timeout_seconds` | `10.0` | Per-request timeout. |
| `max_retries` | `2` | Retry attempts for retryable HTTP or network failures. |
| `retry_backoff_seconds` | `0.25` | Fixed retry backoff for this experimental connector. |

All malformed, oversized, partial, rejected, unauthorized, preview-only, or
ambiguous responses fail closed by raising framework errors. The core runner
then avoids ACK and lets the configured redelivery or DLQ policy decide what
happens next.

## Security Notes

- Keep `endpoint_allowed_hosts` set to the expected Gotham hostname.
- Use HTTPS. Plain HTTP is allowed only for loopback fake-client tests with
  `allow_http_for_local_testing=true`.
- Keep Gotham credentials in environment variables or an approved secret
  manager wrapper outside the repository.
- Do not include private Gotham URLs, object type names, property type names,
  tokens, client identifiers, primary keys, payloads, or operational labels in
  public evidence.
- Treat Gotham responses as untrusted input. Malformed, oversized, missing
  `primaryKey`, retryable, or unexpected status responses do not produce sink
  success.
- Use least-privileged service identities scoped only to the object types and
  property mutations required for the handoff.

## Testing

The local tests use a fake Gotham client that implements the same
`GothamObjectClient` protocol as the HTTP client. It can report success,
duplicates, rejections, temporary failures, and ambiguous partial outcomes
without connecting to Gotham.

Run the focused suite:

```bash
python -m pytest tests/unit/test_gotham_sink.py -q
```

Expected output:

```text
14 passed
```

The fake-client suite proves:

- config validation rejects unsafe endpoints and ambiguous property mappings;
- object mapping preserves payload and selected metadata;
- duplicate external IDs fail closed within one batch;
- rejected or ambiguous client results prevent success;
- temporary client failures remain retryable framework failures;
- runner-level ACK evidence proves Gotham acceptance happens before ACK and
  failures prevent ACK.

This is not live Gotham certification. A future live test must be explicitly
gated, use ignored local configuration, and target only an approved
non-production Gotham environment.
