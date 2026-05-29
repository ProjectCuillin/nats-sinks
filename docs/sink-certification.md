# Sink Certification

Sink certification is the release gate used by `nats-sinks` before a destination
backend may be described as production-ready. It is more than a Python typing
check. A sink can implement the `Sink` protocol and still be unsafe if it
returns success before durable commit, logs sensitive payloads, ignores
redelivery behavior, or hides destination failures behind generic exceptions.

This page defines the shared contract for first-party sinks and future
connectors. It is written for maintainers, external contributors, reviewers,
and operators who need to understand what evidence exists before trusting a
sink in an at-least-once JetStream delivery path.

The current first-party production sinks are:

- [Oracle Sink](oracle-sink.md), where success means the Oracle transaction has
  committed.
- [Oracle MySQL Sink](mysql-sink.md), where success means the Oracle MySQL
  transaction has committed.
- [File Sink](file-sink.md), where success means every configured file has been
  flushed, optionally fsynced, and atomically placed at its final path.
- [Edge Spool Sink](spool-sink.md), where success means the encrypted local
  spool record has been committed.

The [Palantir Foundry Sink](foundry-sink.md) and
[Palantir Gotham Sink](gotham-sink.md) are built in but experimental. They pass
local fake-client certification, but they must not be described as
production-ready until live platform-specific certification evidence exists.
The [Oracle NoSQL Database Sink](oracle-nosql-sink.md) is also built in but
experimental. It uses the official Oracle NoSQL Python SDK in the production
runtime path and has local KVLite container-backed e2e evidence, but the
connector-wide metadata must stay conservative until every production-targeted
deployment and authentication mode has accepted live evidence. Future sinks
must pass the same certification standard before documentation, metadata, or
connector descriptors can mark them as production-ready.

## Certification Model

```mermaid
flowchart LR
    Envelope[NatsEnvelope] --> Sink[Destination sink]
    Sink --> Durable[Durable destination boundary]
    Durable --> Success[write_batch returns success]
    Success --> Core[Core runner ACKs JetStream]

    Tests[Certification tests] --> Sink
    Docs[Certification documentation] --> Review[Release review]
    Review --> Status[Production-ready status]
```

The key invariant remains:

> Core owns delivery semantics. Sinks own destination writes.

That means certification must prove two things at the same time:

1. The sink crosses its own durable success boundary before returning success.
2. The sink never acknowledges, terminates, or negatively acknowledges a NATS
   JetStream message directly.

## Required Evidence

Every production sink must have evidence for the following areas.

| Area | Required evidence |
| --- | --- |
| Lifecycle | `start()`, `write_batch(...)`, and `stop()` are async and safe to call through the core runner. |
| Boundary | `write_batch(...)` returns only after durable destination success. |
| Failure classification | Temporary failures raise `TemporarySinkError` subclasses and permanent failures raise `PermanentSinkError` subclasses where possible. |
| Idempotency | Duplicate redelivery is safe, controlled, and documented for the recommended production mode. |
| Payload handling | JSON, non-JSON text, empty payloads, bytes payloads, and encrypted payload envelopes are handled according to the framework payload contract. |
| Metadata | Standard NATS metadata, priority, classification, labels, mission metadata, and custody metadata are preserved where the sink supports them. |
| Security | Secrets, credentials, payloads, connection strings, private keys, and sensitive metadata are not logged by default. |
| Input validation | Destination identifiers, paths, URLs, object names, table names, and column names are validated with allow lists or destination-native safe APIs. |
| Unit tests | Unit tests are deterministic and never make network calls. |
| Integration tests | Live service tests are isolated behind markers, scripts, or explicit environment flags. |
| Documentation | The sink page explains durable success, failure behavior, idempotency, security, and known limitations. |

## Certification Sequence

```mermaid
sequenceDiagram
    participant T as Certification Test
    participant S as Sink
    participant D as Destination Double
    participant C as Core Contract

    T->>S: start()
    S-->>T: ready
    T->>C: assert NatsEnvelope has no ACK primitives
    T->>S: write_batch(envelopes)
    S->>D: write using safe destination API
    D-->>S: durable success
    S-->>T: return success
    T->>D: assert durable evidence
    T->>S: write_batch(redelivery)
    S->>D: duplicate-safe write
    D-->>S: prior success or idempotent update
    S-->>T: return success
    T->>S: stop()
```

The sequence uses destination doubles in unit tests. Live systems are tested
separately through integration or end-to-end scripts so unit tests stay fast,
deterministic, and safe for contributors.

## Reusable Test Helpers

The package exposes helper functions in `nats_sinks.testing` for sink authors:

```python
from nats_sinks.testing import (
    SinkCertificationCase,
    certification_envelope,
    certify_sink_duplicate_redelivery,
    certify_sink_lifecycle,
    certify_sink_write_success,
)
```

A minimal certification case looks like this:

```python
from pathlib import Path
from collections.abc import Sequence

from nats_sinks import NatsEnvelope, Sink
from nats_sinks.file import FileSink
from nats_sinks.testing import (
    SinkCertificationCase,
    certification_envelope,
    certify_sink_write_success,
)


def file_case(root: Path) -> SinkCertificationCase:
    message = certification_envelope(stream_sequence=1)

    def make_sink() -> Sink:
        return FileSink(directory=root, fsync=False)

    def assert_written(_sink: Sink, messages: Sequence[NatsEnvelope]) -> None:
        assert len(list(root.rglob("*.json"))) == len(messages)

    return SinkCertificationCase(
        name="file",
        sink_factory=make_sink,
        messages=(message,),
        after_write=assert_written,
    )


async def test_file_sink_certification(tmp_path: Path) -> None:
    await certify_sink_write_success(file_case(tmp_path))
```

The helpers intentionally do not decide what durable success means for a sink.
The destination-specific assertion must prove that evidence. For Oracle
Database and Oracle MySQL this can be a fake connection that records
`commit()`. For the file sink this can be the presence and content of the
atomically placed output file.

## Fan-Out Routing Certification

Routing and fan-out have a separate certification path because route selection
is not the same thing as destination durability. The helper module
`nats_sinks.testing` includes deterministic fan-out fixtures that prove:

- one envelope selects the intended route and logical sink targets;
- one-to-many fan-out de-duplicates targets in policy order;
- ACK remains blocked until every required selected target succeeds;
- optional targets use explicit, bounded wait and timeout behavior;
- no-route policies are explicit (`reject`, `ignore`, or `default_route`);
- logs and public evidence do not include payloads or destination secrets.

Use these helpers when adding a new sink, changing routing policy, or changing
fan-out execution code:

```python
from nats_sinks.testing import (
    FanoutAckProbe,
    FanoutCertificationCase,
    FanoutOperationPlan,
    certify_fanout_ack_order,
    fanout_certification_envelope,
    fanout_certification_policy,
)


async def test_required_target_blocks_ack_until_commit() -> None:
    probe = FanoutAckProbe()
    case = FanoutCertificationCase(
        name="secret-route",
        envelope=fanout_certification_envelope(),
        policy=fanout_certification_policy(),
        expected_routes=("nato_secret_sensor_audit",),
        expected_targets=("oracle_secret", "file_audit"),
    )

    result = await certify_fanout_ack_order(
        case,
        (
            FanoutOperationPlan("oracle_secret"),
            FanoutOperationPlan("file_audit"),
        ),
        ack=probe.ack,
    )

    assert probe.called is True
    assert result.ack_gate.required_committed == ("oracle_secret",)
```

The built-in fan-out policy is intentionally synthetic. It uses an urgent
NATO SECRET sensor audit example that selects `oracle_secret` and optional
`file_audit`, and a NATO UNCLASS variant that selects only `oracle_unclass`.
Those names are public examples, not deployment guidance or real operational
identifiers.

Fan-out execution code must continue to use these helpers alongside
destination-specific sink certification. A route can be correct and still be
unsafe if the selected sink returns success before its durable boundary.

## Built-In Sink Certification Status

| Sink | Certification coverage |
| --- | --- |
| Fan-out | Unit tests cover one-to-one dispatch, one-to-many dispatch, required failure after partial success, optional timeout, no-route behavior, runner no-ACK behavior on required failure, and CLI validation for inline fan-out examples. |
| Oracle Database | Unit contract tests cover commit-before-success, rollback on failure, duplicate-safe modes, payload normalization, metadata columns, encryption envelopes, error translation, and SQL identifier validation. Live Oracle tests are opt-in through ignored local environment files. |
| Oracle MySQL | Unit contract tests cover commit-before-success, rollback on failure, duplicate-safe modes, payload normalization, metadata columns, TLS option validation, Oracle MySQL metrics, error translation, and SQL identifier validation. Container-backed e2e tests run against a short-lived Oracle MySQL test database. |
| File | Unit and file e2e tests cover atomic file placement, duplicate handling, gzip compression, payload modes, encrypted payload envelopes, metadata preservation, path sanitization, health checks, and no ACK ownership. |

The reusable helpers are now applied to built-in sinks where practical:

- file sink lifecycle, write, duplicate-redelivery, and log-redaction helper
  coverage,
- Oracle sink durable-success helper coverage using a fake connection pool that
  proves `commit()` occurs before `write_batch(...)` returns,
- Oracle MySQL sink durable-success helper coverage using a fake connection
  pool and the same certification helper pattern.

## Production-Ready Connector Requirements

A future connector may be present in the backlog, documented as experimental,
or exposed as a reviewed plugin without being production-ready. To claim
production status, it must have:

1. A `SinkConnector` descriptor with `production_ready=True`.
2. Documentation that points to the sink page and the certification evidence.
3. Unit tests using the shared certification helpers.
4. Destination-specific tests for idempotency and durable success.
5. Integration or end-to-end tests behind explicit markers or scripts.
6. Security documentation covering secrets, least privilege, logging, and
   input validation.
7. Changelog and release evidence naming the certified surface.

If any of those are missing, the connector should remain experimental,
roadmap-only, or not planned until scope changes.

## What Certification Does Not Claim

Certification does not mean exactly-once delivery. `nats-sinks` provides
at-least-once delivery from JetStream to external destinations with
commit-then-acknowledge processing and idempotent sink support.

Certification also does not replace:

- database migration review,
- cloud IAM review,
- operational acceptance testing,
- performance testing under realistic volume,
- compliance accreditation,
- deployment-specific threat modeling.

It is the baseline evidence that a sink respects the framework contract and is
safe enough to enter release review.

## Contributor Checklist

Before proposing a production sink:

- Add or update the sink module and configuration model.
- Validate all external identifiers and destination-specific inputs.
- Use safe destination APIs, bind variables, SDK calls, or object APIs rather
  than string-concatenated commands.
- Add unit tests with `SinkCertificationCase`.
- Add destination-specific tests for idempotency, duplicate redelivery, and
  failure classification.
- Add integration tests behind explicit markers.
- Add documentation under `docs/`.
- Add examples that use JSON configuration and no committed secrets.
- Add release notes and update the public API contract when new public imports
  are introduced.
- Run:

```bash
scripts/check-sinks.sh
scripts/check.sh
```

Live tests that require Oracle, NATS, or cloud services must remain opt-in and
must never require committed credentials.
