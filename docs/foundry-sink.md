# Palantir Foundry Sink

The Palantir Foundry sink is an experimental first-party connector for Foundry
Streams push-based ingestion. It lets a deployment hand selected JetStream
events to a Foundry stream while keeping the normal nats-sinks delivery
contract: the core runner ACKs only after `FoundrySink.write_batch(...)`
returns success.

The connector is not yet live-certified. It has local fake-client contract
tests and sink certification tests, but production use requires validation
against an approved Foundry environment, approved credentials, and the exact
customer Foundry ingestion surface.

## Research Decision

Issue #150 evaluated the public Foundry documentation and selected Streams
push ingestion as the first supported target. Public Foundry material describes
push-based ingestion for sending records into Foundry streams and datasets, and
the Python SDK documentation exposes stream APIs. The connector uses a narrow
HTTP client boundary instead of depending on a broad SDK surface because stream
push endpoints and authentication details are deployment-specific.

Useful public references:

- [Foundry push-based ingestion](https://www.palantir.com/docs/foundry/data-connection/push-based-ingestion/)
- [Foundry third-party applications and OAuth2](https://www.palantir.com/docs/foundry/platform-security-third-party/)
- [Palantir Python SDK](https://github.com/palantir/palantir-python-sdk)

## Status

| Capability | Status |
| --- | --- |
| Sink type | `foundry` |
| Supported target | Foundry Streams push ingestion |
| Production readiness | Experimental |
| Local certification | Unit and fake-client contract tests |
| Live certification | Not performed by default |
| Optional dependency | None for the current HTTP client layer |

## Minimal Configuration

Use an environment variable for the bearer token. The token value must not be
stored in JSON configuration, documentation, shell history, issue comments, or
test reports.

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "MISSION_EVENTS",
    "consumer": "foundry-sink",
    "subject": "mission.events.>"
  },
  "sink": {
    "type": "foundry",
    "stream_push_url": "https://foundry.example.invalid/api/push/streams/example",
    "bearer_token_env": "FOUNDRY_TOKEN",
    "endpoint_allowed_hosts": ["foundry.example.invalid"],
    "batch_size": 100,
    "timeout_seconds": 10
  }
}
```

Then run the normal validation path:

```bash
nats-sink validate foundry-config.json
```

Example output:

```text
Configuration is valid.
Active sink: foundry
ACK policy: commit-then-acknowledge
```

## OAuth2 Client Credentials

Foundry deployments should prefer reviewed service identities and
least-privilege application permissions. When the environment provides OAuth2
client credentials, configure only environment-variable names:

```json
{
  "sink": {
    "type": "foundry",
    "stream_push_url": "https://foundry.example.invalid/api/push/streams/example",
    "auth_mode": "oauth2_client_credentials",
    "oauth2_token_url": "https://foundry.example.invalid/oauth2/token",
    "oauth2_client_id_env": "FOUNDRY_CLIENT_ID",
    "oauth2_client_secret_env": "FOUNDRY_CLIENT_SECRET",
    "oauth2_scope": "api:use-streams-write",
    "endpoint_allowed_hosts": ["foundry.example.invalid"]
  }
}
```

The connector never logs resolved token values, client secrets, endpoint URLs,
or response bodies. Runtime errors report only sanitized status summaries such
as `Foundry stream push returned permanent HTTP 401`.

## Record Shape

Each NATS envelope is mapped to a Foundry stream record using the public
Foundry push record shape:

```json
{
  "value": {
    "nats_sinks_record_key": "stream-sequence:MISSION_EVENTS:42",
    "subject": "mission.events.sensor",
    "payload": {
      "event_id": "example-event",
      "status": "observed"
    },
    "payload_info": {
      "original_format": "json",
      "wrapped": false,
      "sha256": "hex-encoded-sha256",
      "size_bytes": 48
    },
    "priority": "high",
    "classification": "nato-unclassified",
    "labels": "sensor,foundry",
    "labels_list": ["sensor", "foundry"],
    "metadata": {
      "schema": "nats_sinks.metadata.v1"
    },
    "mission_metadata": null,
    "security_labels": null,
    "custody": null
  }
}
```

Payload handling uses the same `NatsEnvelope.payload_for_json_storage(...)`
contract as Oracle Database, Oracle MySQL, file, and future JSON-capable
sinks. JSON is preserved, non-JSON text is wrapped, and binary payloads are
base64 wrapped unless `payload_mode` is configured differently.

## Idempotency

The default `record_key_strategy` is `idempotency_key`. It uses the stable
framework key:

- `stream-sequence:<stream>:<sequence>` when JetStream stream sequence metadata
  is present;
- `message-id:<message-id>` when a publisher message ID is available;
- `payload-sha256:<subject>:<digest>` as the fallback.

The sink rejects duplicate record keys inside one batch because a partial or
ambiguous push result would make ACK decisions unsafe. Duplicate redelivery of
the same message remains safe when Foundry accepts the same deterministic
record again or reports it as an idempotent duplicate through the client
contract.

Other supported strategies are:

- `stream_sequence`
- `message_id`
- `payload_sha256`

Strategies that require missing metadata fail closed with a permanent sink
error, allowing the normal DLQ-before-ACK path when DLQ is configured.

## Limits

| Setting | Default | Purpose |
| --- | ---: | --- |
| `batch_size` | `100` | Maximum records per Foundry request. |
| `timeout_seconds` | `10` | Per-request timeout. |
| `max_retries` | `2` | Bounded connector-side retries for retryable HTTP status. |
| `retry_backoff_seconds` | `0.25` | Small bounded wait between connector-side retries. |
| `max_record_bytes` | `262144` | Per-record JSON size limit. |
| `max_batch_bytes` | `4194304` | Per-request JSON size limit. |
| `max_response_bytes` | `65536` | Response body read limit. |

These limits protect local memory and prevent a compromised publisher from
using the sink as an unbounded HTTP request generator. The core delivery retry
policy still controls JetStream redelivery after the sink raises a framework
error.

## Security Notes

- Keep Foundry credentials in environment variables or a protected service
  environment file.
- Use `endpoint_allowed_hosts` so configuration review can confirm the intended
  Foundry host.
- Do not include private Foundry URLs, resource identifiers, tokens, client
  identifiers, response bodies, or payloads in issue comments or test reports.
- Treat Foundry responses as untrusted. Partial, rejected, malformed, oversized,
  or ambiguous responses fail closed.
- Do not claim production certification from mock tests alone.

## Local Test Harness

The local tests use a fake Foundry client that implements the same
`FoundryStreamClient` protocol as the HTTP client. It can report success,
validation rejection, authorization-style rejection, throttling, ambiguous
partial acceptance, temporary failure, and duplicate redelivery without
connecting to Foundry.

Run the focused suite with:

```bash
python -m pytest tests/unit/test_foundry_sink.py -q
```

Example output:

```text
..............
14 passed
```

Run the public import contract as well:

```bash
python -m pytest tests/unit/test_public_api.py -q
```

The fake-client suite is useful release evidence. It is not live Foundry
certification.
