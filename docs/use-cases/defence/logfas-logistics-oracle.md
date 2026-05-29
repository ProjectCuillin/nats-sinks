# Persisting LOGFAS-Related Mission Logistics Events To Oracle Database

## Context

LOGFAS-style systems support logistics, deployment, movement planning,
sustainment, and force support workflows. In this use case, `nats-sinks`
persists authorized logistics event data from NATS JetStream into Oracle
Database for audit, reporting, planning support, synchronization, and
downstream mission-support analytics.

This page does not claim direct integration with protected LOGFAS interfaces.
It describes a generic persistence pattern for authorized logistics events that
have already been exposed by an approved logistics integration service.

```mermaid
flowchart LR
    Adapter[Logistics adapter or integration service] --> JS[NATS JetStream]
    JS --> Sink[nats-sinks]
    Sink --> Oracle[Oracle Database]
    Oracle --> Consumers[Reporting / planning / analytics / audit]
```

## Example Logistics Events

Generic, non-sensitive event families include:

- movement request created;
- convoy status updated;
- shipment milestone reached;
- asset allocation changed;
- sustainment demand updated;
- transportation plan synchronized;
- logistics exception raised.

Use fake identifiers in examples and test data. Do not place real unit data,
movement plans, asset identifiers, locations, or protected operational details
in public documentation, GitHub Issues, test reports, or logs.

## Proposed Architecture

1. An approved logistics integration service receives or derives authorized
   logistics events from the source workflow.
2. The integration service normalizes the event into an internal envelope and
   publishes it to NATS JetStream.
3. `nats-sinks` validates the event envelope and writes it to Oracle Database.
4. Oracle Database supports reporting, audit, synchronization, planning
   support, and downstream mission-support analytics.

This separation lets source logistics systems publish events once while
allowing Oracle Database, reporting tools, Python consumers, Java consumers,
and other approved services to work from a durable event history.

## Processing Model

`nats-sinks` uses commit-then-acknowledge processing:

1. receive the logistics event from JetStream;
2. validate and normalize the envelope;
3. write the event to Oracle Database;
4. commit the Oracle transaction durably;
5. ACK the JetStream message only after successful commit.

Assume at-least-once delivery. Idempotent writes should use a deterministic
identity such as:

- logistics event ID;
- source system identifier;
- source transaction ID;
- movement ID;
- correlation ID;
- payload hash.

On duplicate delivery, the database should update existing processing state or
perform a no-op instead of creating duplicate logistics records. Temporary
failures should be retried through redelivery. Permanent validation failures
should use dead-letter handling where configured.

## Example LOGFAS-Style Event Envelope

The following example is generic and unclassified. It is intended to show shape
and persistence responsibilities, not a protected LOGFAS interface contract.

```json
{
  "event_id": "example-logistics-event-0001",
  "source_system": "authorized-logistics-integration-service",
  "logistics_domain": "movement-support",
  "event_type": "movement_request_created",
  "movement_id": "movement-placeholder-001",
  "unit_reference": "unit-placeholder",
  "asset_reference": "asset-placeholder",
  "event_timestamp": "2026-05-29T11:20:00Z",
  "received_timestamp": "2026-05-29T11:20:02Z",
  "status": "created",
  "location_reference": "location-placeholder",
  "classification_label": "NATO UNCLASSIFIED",
  "payload_version": 1,
  "normalized_payload": {
    "schema": "example.logistics.event.v1",
    "planning_window": "placeholder",
    "movement_category": "placeholder",
    "milestones": [
      {
        "name": "example-milestone",
        "state": "planned"
      }
    ]
  },
  "processing_metadata": {
    "ingest_profile": "logfas-logistics-oracle",
    "normalizer_version": "example-1",
    "idempotency_key": "authorized-logistics-integration-service:example-logistics-event-0001"
  }
}
```

## Oracle Database Persistence

Oracle Database can hold stable logistics metadata in columns and
profile-specific logistics context in JSON. A production schema should be
defined by the authorized programme team, but table responsibilities commonly
include:

| Responsibility | Example Table | Purpose |
| --- | --- | --- |
| Logistics event history | `logistics_event` | Event ID, source system, event type, domain, timestamps, classification label, idempotency key, and correlation fields. |
| Movement context | `logistics_movement` | Movement reference, planning status, synchronization state, and approved movement metadata. |
| Asset status | `logistics_asset_status` | Asset reference, allocation state, milestone state, and current support status. |
| Processing state | `logistics_processing_state` | Sink write status, retry count, last attempt, DLQ status, and recovery markers. |
| Audit history | `logistics_audit` | Duplicate detection, retries, failures, schema-version changes, and operator-reviewed notes. |

This is non-normative. Some deployments may store the complete normalized
event in one append-only Oracle table with a JSON column. Others may split
movement, asset, and audit views to match reporting, retention, and access
control requirements.

## Operational Considerations

- Keep source logistics interfaces, authorization, and domain mapping in the
  logistics integration layer.
- Keep `nats-sinks` focused on envelope validation, durable Oracle writes,
  retry behavior, DLQ handling, and commit-then-acknowledge custody.
- Version logistics payloads so planning and reporting consumers can tolerate
  schema evolution.
- Do not assume strict global ordering across distributed logistics adapters.
  Persist source timestamps, receive timestamps, source transaction IDs, and
  movement correlation IDs for reconstruction.
- Treat classification labels, movement IDs, unit references, asset
  references, and location references as sensitive handling metadata in real
  deployments.
- Deploy into an isolated defence cloud or mission support environment where
  required, and prefer containerized by default service packaging.
- Manage Oracle, NATS, and integration-service credentials through a dedicated
  secret store such as HashiCorp Vault where applicable.
- Use Consul or an equivalent approved service registration and discovery
  mechanism where applicable, while keeping database privileges
  least-privileged and configuration reviewable.
- Where Kafka is used as an inter-node or wider enterprise transport layer,
  use an approved bridge or adapter to publish normalized logistics events into
  JetStream before persistence.

## Benefits

- Durable logistics event history in Oracle Database.
- Improved operational auditability and replay support.
- Better support for planning, reporting, and downstream mission-support
  analytics.
- Decoupling of source logistics systems from downstream consumers.
- Resilient persistence under retry and failure scenarios.
- A stable event foundation for Python or Java integration components where
  relevant.

## Boundaries

This page is conceptual. It does not contain real unit data, real movement
plans, classified operational details, or protected LOGFAS interface
specifications. Actual event mappings, interface permissions, schemas,
retention policies, and data-handling controls must be defined by authorized
programme teams.
