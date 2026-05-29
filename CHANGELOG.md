# Changelog

All notable changes to this project will be documented in this file.

The format follows Keep a Changelog, and this project uses semantic versioning.

Repository: [ProjectCuillin/nats-sinks](https://github.com/ProjectCuillin/nats-sinks/)

Named contributor: Johan Louwers, [louwersj@gmail.com](mailto:louwersj@gmail.com).

## [Unreleased]

### Added

- Added the first-party HTTP sink for issue #17. The new `http` sink type
  forwards normalized envelopes or payload JSON to one fixed
  operator-configured endpoint, validates HTTPS and loopback-only local HTTP
  usage, static and environment-backed headers, response classifications,
  request and response size limits, bounded retries, and explicit
  idempotency-key propagation, while documenting timeout ambiguity and the
  requirement that HTTP endpoints be idempotent under at-least-once redelivery.
- Added the first-party S3-compatible object sink for issue #39. The new `s3`
  sink type writes normalized envelopes or payload JSON to deterministic
  object keys, validates buckets, prefixes, endpoints, credential-source
  references, object suffixes, metadata, object sizes, and retry budgets,
  supports `skip_existing`, `replace`, and `fail_existing` duplicate
  policies, optional metadata sidecars, optional gzip compression, and
  provider-managed `AES256` server-side encryption requests, keeps boto3
  behind the optional `s3` extra, includes fake-client unit and certification
  coverage without network calls, and documents least privilege, privacy, and
  live-test gating.
- Added defence and mission-support documentation blueprints for persisting
  authorized Link 16 / TADIL-J J-series tactical message events and
  LOGFAS-related mission logistics events into Oracle Database. The pages keep
  radio, cryptographic, tactical, protected-interface, and classified semantics
  outside the project scope while documenting commit-then-ACK persistence,
  idempotent Oracle writes, retry and DLQ handling, security labelling, and
  isolated defence cloud deployment considerations.
- Added explicit headers-only payload-presence handling and confirmed
  acknowledgement controls for issues #111, #112, #113, #114, #115, and #116.
  `NatsEnvelope` now distinguishes producer-empty payloads from JetStream
  headers-only body omission, standard metadata and DLQ records persist the
  payload-presence state, payload-hash idempotency fallback is rejected when a
  body was omitted, `delivery.ack_confirmation` can opt into bounded
  server-confirmed ACKs after durable sink or DLQ success, and
  `nats-sink-metrics` exposes low-cardinality ACK confirmation counters and
  timing observations.
- Added the full local container-backed key/value sink e2e gate for issue
  #316. Setting `NATS_SINKS_RUN_CONTAINER_E2E=1` now makes
  `scripts/check-sinks.sh` run the maintained Oracle NoSQL Database sink e2e
  container helper and the Oracle Coherence Community Edition sink e2e
  container helper in one opt-in local release-validation pass, while normal
  checks still avoid Docker and optional backend SDK requirements by default.
- Added the local Oracle NoSQL Database KVLite test backend for issue #310
  and the container-backed Oracle NoSQL sink e2e harness that revisits issue
  #149. The new helpers use Oracle's documented Community Edition image from
  GitHub Container Registry, bind the HTTP proxy to a random loopback port,
  verify one complete fake event JSON key/value row, run the Oracle NoSQL sink
  live-gated integration test against the short-lived backend, clean up by
  default, and document the local-only non-secure KVLite boundary.
- Added the Oracle NoSQL Database production-readiness certification package
  for issue #319. The documentation now separates the real SDK-backed
  production runtime from connector-wide production-ready status, lists the
  supported deployment and authentication modes in a certification matrix,
  documents live Cloud Simulator and Oracle NoSQL Database Cloud Service
  runbook inputs, and keeps the connector metadata experimental until accepted
  live evidence exists for production-targeted modes.
- Added the experimental first-party Oracle NoSQL Database sink for issue
  #149. The new `oracle_nosql` sink type stores one complete normalized event
  JSON object in a configured Oracle NoSQL table value field, validates SDK
  endpoints, deployment/auth modes, table and field identifiers, key prefixes,
  generated table DDL, duplicate policies, timeouts, and row size limits,
  derives deterministic keys from approved idempotency metadata, supports
  `skip_existing`, `replace`, and `fail_existing` duplicate behavior, keeps
  the Oracle NoSQL Python SDK behind the optional `oracle-nosql` extra,
  includes fake-client unit and certification tests, and documents live KVLite
  or Cloud Simulator gating for future container-backed validation.

- Added the deterministic multi-sink routing end-to-end flow for issue #301.
  The new `scripts/run-multi-sink-routing-e2e.py` runner validates the tracked
  fan-out config, drives the production `FanoutSink` with local file-backed
  probe sinks, proves subject, priority, classification, label, header, and
  static gate matching across Oracle Database, Oracle MySQL Database, File,
  and Oracle Coherence Community Edition logical targets, exercises one-to-one
  routing, one-to-many fan-out, no-route handling, optional sink timeouts,
  required sink failure after partial success, duplicate redelivery safety,
  and writes sanitized pipe-friendly report output without payloads,
  credentials, destination details, or local paths.
- Added the experimental first-party Oracle Coherence Community Edition sink
  for issue #302. The new `coherence` sink type stores one complete normalized
  event JSON object as a configured Coherence cache or map value, validates
  cache names, key prefixes, serializer mode, TTLs, duplicate policy,
  timeouts, and value limits, derives deterministic keys from approved
  idempotency metadata, supports `skip_existing`, `replace`, and
  `fail_existing` duplicate behavior, keeps the Coherence Python client behind
  the optional `coherence` extra, includes fake-client unit and certification
  tests plus a local container-backed e2e runner, and documents when Coherence
  can be ACK-gated custody versus an optional fan-out read-model target.
- Added a local Oracle Coherence Community Edition test backend for issue
  #303. The new Oracle Linux 9 slim based Dockerfile and smoke runner resolve
  explicit Coherence CE runtime modules during build, start a short-lived
  backend with random local naming and loopback port selection, verify one
  complete fake event JSON object as a key/value entry through the optional
  Coherence Python client, clean up by default, and document the backend as
  test infrastructure for future sink and routing work.
- Added the experimental Palantir Gotham RevDB object sink for issue #151. The
  new `gotham` sink type targets Gotham object creation through a narrow HTTP
  client boundary, validates endpoint allow-lists, environment-backed bearer
  token or OAuth2 client-credentials auth, object type names, property type
  mappings, security markings, batch and response limits, maps normalized
  payload and selected metadata into Gotham object-create requests, includes
  fake-client contract tests and sink certification, and documents that mock
  certification is not live Gotham certification.
- Added the experimental Palantir Foundry Streams sink for issue #150. The new
  `foundry` sink type targets push-based stream ingestion through a narrow HTTP
  client boundary, validates endpoint allow-lists, environment-backed bearer
  token or OAuth2 client-credentials auth, record field names, batch and
  response limits, maps normalized payload and metadata into Foundry records,
  includes fake-client contract tests and sink certification, and documents
  that mock certification is not live Foundry certification.
- Added the disabled-by-default Amazon CloudWatch observability connector for
  issue #102. The new `cloudwatch` policy section exports only approved local
  metrics snapshot rows through bounded `PutMetricData` request shapes, keeps
  boto3 behind the optional `cloudwatch` extra, supports dry-run JSON output
  without AWS credentials, validates namespace, region, dimensions, request
  size, retries, and stale-snapshot behavior, suppresses prepared labels as
  dimensions unless explicitly enabled, and documents IAM, cost, throttling,
  service separation, and testing guidance.
- Added the disabled-by-default Azure Monitor observability connector for
  issue #103. The new `azure_monitor` policy section exports only approved
  local metrics snapshot rows through bounded Azure Monitor custom metrics
  REST request shapes, uses an environment-backed Microsoft Entra bearer token
  without adding an Azure SDK dependency, supports dry-run JSON output without
  tokens or Azure resource IDs, validates resource IDs, locations, namespaces,
  dimensions, request size, retries, and stale-snapshot behavior, suppresses
  prepared labels as dimensions unless explicitly enabled, and documents
  identity, resource scope, throttling, service separation, and testing
  guidance.
- Added the disabled-by-default Datadog observability connector for issue #104.
  The new `datadog` policy section exports approved local metrics snapshots as
  bounded DogStatsD datagrams to a local or approved Datadog Agent listener,
  supports dry-run output without Datadog API credentials, validates transport,
  metric prefixes, low-cardinality static tags, datagram sizes, retries, and
  stale-snapshot behavior, suppresses prepared metric labels as tags unless
  explicitly enabled, and documents Agent operation, tag confidentiality,
  cardinality, and testing guidance.
- Added effective consumer-policy guardrails for optional JetStream
  `InProgress` heartbeats for issue #117. The runner now allows `bind_only`
  deployments to verify AckWait from the existing durable consumer before
  fetching, re-checks created or reconciled consumers when progress heartbeats
  are enabled, rejects effective BackOff policies until BackOff-aware timing is
  supported, and fails closed when durable consumer timing cannot be verified.
- Added optional JetStream `InProgress` heartbeats for issue #118. The new
  `delivery.in_progress` configuration is disabled by default, starts only
  while `sink.write_batch(...)` is active, stops before final ACK, NAK, Term,
  retry, DLQ, cancellation, or shutdown completion, and fails closed unless
  safe effective AckWait timing is verified with a heartbeat interval below 80%
  of AckWait. BackOff-based consumer timing remains rejected until explicit
  BackOff-aware heartbeat support is implemented.
- Added stable InProgress observability metrics and an operator runbook for
  issue #119. The new metric contract covers progress attempts, successful
  progress signals, failed progress signals, maximum-heartbeat exits, active
  heartbeat batches, and heartbeat timing, with `nats-sink-metrics` shell,
  table, and Prometheus rendering guidance that keeps payloads, subjects,
  destinations, and classification details out of metric output.
- Added durable replay-to-sinks guidance and tooling design for issue #120.
  The new documentation separates ordered inspection from write-capable replay,
  requires durable pull consumers and commit-then-ACK behavior for replay into
  sinks, documents start sequence, start time, subject scope, maximum message,
  dry-run, redacted report, and idempotency review boundaries, and adds a
  documentation guardrail test for the replay contract.
- Added the read-only ordered-consumer inspection CLI for issue #122. The new
  `nats-sink inspect-ordered` command uses the installed `nats-py`
  ordered-consumer API when available, fails closed when client support is
  missing, never builds or writes a sink, redacts payloads and sensitive
  headers by default, validates message, payload-byte, pending, timeout, and
  JSONL output-path limits, and documents that ordered inspection is not
  durable sink replay.
- Added an explicit ordered-consumer client compatibility result for issue
  #121. The inspection path now names supported, unsupported, non-callable,
  partial, and ambiguous NATS client capability states through sanitized
  fail-closed reasons while leaving the production durable pull runner
  unchanged.
- Added push-consumer guardrails and opt-in runner support for issues #123 and
  #125. The new `push_consumer` configuration is disabled by default, requires
  manual ACK, validates deliver subjects, deliver groups, pending message and
  byte limits, rejects pull-only `max_waiting` settings in push mode, detects
  required `nats-py` push-subscribe capabilities, and routes accepted callback
  messages through the existing commit-then-ACK batch pipeline with bounded
  queue overflow handling.
- Added push-consumer delivery-contract certification tests for issue #124.
  The focused suite proves ACK-after-commit ordering, no ACK on temporary sink
  failure, DLQ publication before original ACK on permanent failure, callback
  exception containment, flow-control and idle-heartbeat option propagation,
  queue overflow handling, cooperative shutdown behavior, and an environment-
  gated live NATS integration path for disposable local servers.
- Added the generic route-match policy selector for issue #138. The new
  disabled-by-default `routing` configuration can match normalized
  `NatsEnvelope` subject, priority, classification, labels, and approved
  non-secret headers, validates NATO SECRET and NATO UNCLASS examples through
  `nats-sink validate`, and exposes public selector helpers for active
  fan-out delivery.
- Added optional ACK-gating policy primitives for issue #137. Route targets
  are required by default, optional target objects can define bounded
  `minimum_wait_ms` and `timeout_ms` behavior, per-sink-type defaults are
  applied and visible in redacted effective config, and the new core ACK-gate
  helper records optional success, failure, or timeout without weakening the
  commit-then-ACK rule for required targets.
- Added named multi-sink instance configuration for issue #136. Config files
  can now declare a top-level `sinks` registry with multiple named Oracle
  Database, Oracle MySQL, file, or spool instances while preserving the
  existing single active `sink` runtime path. The CLI validates every named
  sink, reports route-to-target references, redacts secrets without hiding
  route target names such as `oracle_secret`, and can health-check one named
  sink with `--sink-name` or all named sinks with `--all-named-sinks`.
- Added routing and fan-out certification tests for issue #135. The new
  `nats_sinks.testing` helpers use synthetic envelopes and in-memory fan-out
  operation plans to certify one-to-one routing, one-to-many target selection,
  required ACK blocking, optional timeout behavior, no-route handling, CLI
  validation, and redaction for fan-out-capable sinks.
- Added fan-out observability metrics and sanitized logging helpers for issue
  #134. The new aggregate metrics cover route matches, routed and no-route
  messages, selected child sink counts, required child success or failure,
  optional child success, failure, or timeout, ACK eligibility, ACK blocking,
  ACK-gate wait time, and fan-out batch duration, with `nats-sink-metrics`
  CLI coverage and documentation that keeps subjects, sink names, labels,
  classifications, payloads, and destination details out of metrics by
  default.
- Added the production fan-out sink orchestration layer for issue #133. The
  new active `sink.type: "fanout"` mode binds route-selected logical targets
  to named child sinks, dispatches each normalized envelope to one or more
  required or optional destinations, blocks runner ACK when any required child
  sink fails after partial success, supports bounded optional side-copy waits,
  validates the compact inline NATO SECRET and NATO UNCLASS example through
  `nats-sink validate`, and documents that fan-out is at-least-once and
  idempotent rather than an atomic distributed transaction across
  destinations.
- Added the subject-aware observability policy model for issue #128. The new
  disabled-by-default `subject_metrics` policy block uses default-deny
  subject-family rules, validates stable operator labels, caps subject-family
  cardinality, defines deterministic overflow behavior, supports label,
  redacted, hash, and explicitly reviewed raw display modes, and provides a
  fail-closed evaluator for future connectors without changing current
  aggregate metric export or delivery behavior.
- Added bounded subject-family metric aggregation for issue #126. The new
  prepared `labeled_metrics` snapshot rows map approved subjects to reviewed
  `subject_family` labels, keep aggregate counters unchanged, enforce
  deterministic overflow handling, and let Prometheus, OTLP-backed profiles,
  Splunk HEC, StatsD, and syslog render only low-cardinality approved family
  labels instead of raw subjects.
- Added subject-aware observability certification tests and runbook guidance
  for issue #127. The new reusable `nats_sinks.testing` helpers use synthetic
  subjects to prove disabled-by-default behavior, allow and deny handling,
  malformed policy rejection, cardinality caps, sanitized connector and
  `nats-sink-metrics` output, and delivery non-interference before
  subject-family metrics are enabled.
- Added the OCI Monitoring observability connector for issue #107. The new
  optional `nats-sinks[oci]` extra keeps the OCI SDK out of the base install,
  adds disabled-by-default `oci_monitoring` policy controls, renders sanitized
  `PostMetricData` dry-run requests, supports instance principals, resource
  principals, or protected OCI SDK config files, enforces bounded dimensions,
  retries, stale-snapshot checks, and request sizes, and documents OCI-native
  custom metric export as an Observability sub-page.
- Added a local-only post-release PyPI artifact validation harness for issue
  #252. The script builds a short-lived Oracle Linux 9 slim validation
  container, installs `nats-sinks` from PyPI instead of the local checkout,
  verifies CLI/import/config/FileSink/metrics behavior, supports explicit
  versions and optional extras, and writes sanitized local reports under
  `.local/pypi-release-validation/`.

### Fixed

- Fixed Oracle NoSQL Database cloud SDK handle construction for issue #320 by
  applying configured `sink.compartment_id` through the SDK handle
  configuration when supported. A focused regression now proves namespace and
  compartment defaults are both passed without making network calls.
- Fixed the issue #317 deterministic test-loader regression found while adding
  the full container-backed e2e gate. The new test module now registers the
  dynamically loaded script module before executing it so dataclass processing
  succeeds without Docker or optional backend SDKs.
- Fixed Oracle NoSQL Database KVLite test backend readiness for issue #313 by
  waiting for SDK-level table/write/read readiness after the proxy TCP port
  opens. This prevents the local smoke and sink e2e helpers from racing a
  proxy socket that accepts connections before Oracle NoSQL SDK requests are
  ready.
- Fixed GitHub CI compatibility with Ruff `PLW0108` by removing an unnecessary
  connector entry-point sort lambda while preserving deterministic connector
  loading behavior.

### Removed

- Removed the standalone sink candidate research page from the public
  documentation navigation so sink planning stays in managed backlog issues and
  roadmap summaries.

## [0.4.1] - 2026-05-25

### Added

- Added maintainer release guidance and a managed backlog item for a future
  local-only post-release PyPI artifact validation harness. The planned harness
  will install the latest released `nats-sinks` package from PyPI inside a
  short-lived container, verify that the local checkout is not being imported,
  run meaningful artifact smoke checks, and require GitHub bug reports for any
  findings before fixes start.
- Added a solo-maintainer branch-protection policy for `main` so release pull
  requests remain gated by PR governance, dependency review, resolved
  conversations, and force-push/deletion protections without requiring
  impossible self-approval.
- Updated release pull request gate workflows to run on release PR branch
  updates, keeping ordinary branch pushes quiet while allowing branch
  protection to see fresh required checks on the latest release commit.
- Added the first-party Oracle MySQL sink for issue #101, including
  `nats_sinks.mysql.MySqlSink`, optional `nats-sinks[mysql]` dependency
  metadata, strict identifier validation, bound SQL values, TLS CA/client
  certificate options, subject-to-table routing, idempotent `upsert` and
  `insert_ignore` modes, payload envelope handling for non-JSON bodies, message
  metadata preservation, Oracle MySQL duplicate/upsert metrics, container-backed
  e2e certification, examples, and dedicated documentation.
- Added optional core message authenticity verification before sink writes,
  including subject-scoped HMAC-SHA256 and Ed25519 rules, canonical signed
  payload and metadata documents, sanitized verification failures,
  DLQ-before-ACK rejection handling, aggregate authenticity metrics, public
  producer helper APIs, configuration validation, tests, and operator
  documentation.
- Added an Elastic Observability profile connector over the shared OTLP
  observability core, including disabled-by-default policy controls,
  Elastic-safe data stream routing hints, environment-sourced header values,
  dry-run rendering, bounded retries and request sizes, CLI support through
  `nats-sink-observe elastic-export`, focused tests, and an Observability
  documentation sub-page.
- Added a Grafana Alloy observability profile over the shared OTLP core,
  including disabled-by-default policy controls, generated Alloy River
  configuration snippets, environment-sourced header and upstream credential
  references, dry-run rendering, bounded retries and request sizes, CLI support
  through `nats-sink-observe grafana-alloy-export` and
  `nats-sink-observe grafana-alloy-config`, focused tests, and an
  Observability documentation sub-page.
- Added a Splunk HEC observability connector for approved aggregate metrics,
  including disabled-by-default policy controls, environment-sourced HEC token
  handling, HEC metric event rendering, TLS verification enforcement, dry-run
  rendering, bounded retries and request sizes, CLI support through
  `nats-sink-observe splunk-hec-export`, focused tests, and an Observability
  documentation sub-page.
- Added a StatsD observability connector for approved best-effort metric
  datagrams, including disabled-by-default policy controls, UDP and Unix
  datagram transport modes, safe metric-name normalization, dry-run rendering,
  bounded datagram sizes, bounded retries, CLI support through
  `nats-sink-observe statsd-export`, focused tests, and an Observability
  documentation sub-page.
- Added a syslog observability bridge for approved RFC 5424-style aggregate
  metric messages, including disabled-by-default policy controls, UDP and Unix
  datagram transport modes, bounded message sizes, structured-data escaping,
  dry-run rendering, bounded retries, CLI support through
  `nats-sink-observe syslog-export`, focused tests, and an Observability
  documentation sub-page.
- Added centralized NATS authentication and TLS connection option construction
  for username/password, token, credentials-file, NKEY seed-file, local CA TLS,
  and TLS client certificate workflows, including identity-path redaction,
  focused unit coverage, and an environment-gated live authentication
  integration test scaffold.
- Added a cross-domain handoff package blueprint with a bounded manifest
  schema, sanitized example package files, SHA-256 validation evidence,
  documentation links from the defence and file-sink guidance, and explicit
  non-goals stating that `nats-sinks` is not a cross-domain guard or
  certification boundary.
- Added Oracle Linux 9 slim as the required base image for the local
  `nats-sinks` Docker image, replacing the previous Debian-based
  `python:3.12-slim` base while preserving the non-root entry point and local
  Docker/NATS smoke-test workflow.
- Added a local Docker image and JSON Compose smoke-test stack for issue #12,
  including a non-root `nats-sink` image, a NATS JetStream service, a
  file-sink configuration, a `scripts/run-docker-local-smoke.py` runner that
  builds the image, publishes test messages, verifies persisted files, and
  avoids local NATS port collisions, plus Docker documentation and unit tests.
- Added production container hardening for issue #223, including fixed
  non-root UID/GID `10001`, read-only-root-compatible local Compose settings,
  stricter Docker build metadata, explicit OCI labels, no image-level
  healthcheck side effects, expanded `.dockerignore` exclusions, deterministic
  Docker asset tests, and detailed operator guidance for writable paths, SBOM
  evidence, vulnerability scanning, provenance, and defence-oriented
  accreditation caveats.
- Added a hardened local Oracle MySQL test database container for issue #247,
  including an Oracle Linux 9 slim based Dockerfile, explicit Oracle MySQL
  9.7.0 LTS package selection, generated per-run test credentials,
  loopback-only random port exposure, cleanup-by-default behavior, a Docker
  smoke runner that verifies table creation plus one insert/read cycle, unit
  tests for the container assets, and dedicated documentation for future
  Oracle MySQL sink development.
- Added guarded non-main pull request auto-approval tooling for ready issue,
  feature, and bug branches raised by the local workflow. The helper refuses
  release pull requests targeting `main`, can verify the expected PR author,
  supports opt-out for manual inspection, and is documented as convenience for
  development branches rather than a substitute for release approval.
- Added pull request label synchronization for the local branch workflow. The
  `scripts/open-release-pr.sh` helper now copies searchable GitHub labels from
  managed source issues to issue, feature, and bug pull requests by default,
  with explicit `--issue` support and a standalone `scripts/sync-pr-labels.py`
  helper for dry-run diagnostics.
- Added a quiet hierarchical branch development and release workflow with
  release development branches, issue branches, bug sub-branches, configurable
  pull request bases, manual release-validation dispatch, pull request
  governance checks, CODEOWNERS review, branch protection tooling, and release
  workflow validation that tags are cut only from commits already merged into
  `main`.
- Added guarded pull request merge evidence tooling. Maintainers can now use
  `scripts/merge-pr-with-comment.py` to validate and post a sanitized
  test-evidence comment before invoking `gh pr merge`, preventing silent local
  PR merges in the release branch workflow.
- Added an optional data-centric security label profile that carries structured
  releasability, handling caveats, owner, originator, policy identifier, and
  retention category metadata through the core runtime, file sink JSON records,
  Oracle `SECURITY_LABELS_JSON`, and the generic metadata snapshot.
- Added strict security-label validation for JSON parsing, duplicate keys,
  root-field allow lists, size limits, optional controlled vocabularies, and
  DLQ-before-ACK behavior on invalid publisher-provided profile headers.
- Added optional Oracle high-throughput staging-table merge mode for `merge`
  and `insert_ignore` writes, including validated staging configuration,
  staging-table DDL helpers, rollback-safe transaction handling, duplicate
  metrics support, unit coverage, and operator documentation.
- Added Oracle `merge_update_columns` controls so operators can preserve the
  existing "update all non-key columns" behavior, restrict matched-row updates
  to selected columns, or leave matched rows unchanged, with validated SQL
  identifier handling and unit coverage.
- Added Oracle per-route idempotency overrides so subject-to-table routes can
  inherit the sink default or use route-specific stream-sequence, message-ID,
  or payload-field keys with compatible merge update controls.
- Added a safe sink connector framework with `SinkConnector` metadata,
  first-party Oracle and FileSink descriptors, explicit `SinkRegistry`
  registration, disabled-by-default allow-listed Python entry-point discovery
  for reviewed external connectors, plugin configuration validation, and public
  API compatibility coverage.
- Added a documented sink certification contract and reusable
  `nats_sinks.testing` helpers for lifecycle, durable write success,
  duplicate redelivery, ACK-boundary protection, and log-redaction checks, with
  Oracle and file sink coverage included in the deterministic sink check suite.
- Added researched backlog items for first-party Oracle-family sink candidates:
  OCI Object Storage, Oracle Berkeley DB, Oracle NoSQL Database, and OCI
  Streaming.
- Added high-priority Palantir Foundry and Palantir Gotham sink backlog items,
  each requiring local fake-client or contract-harness testing before any live
  certification claim.
- Added low-priority research backlog items for common Kafka-style destination
  patterns: Elasticsearch or OpenSearch, Snowflake, BigQuery, Azure object
  storage, Kafka, MongoDB, Redis, and Cassandra-compatible stores.
- Added `PayloadKeyRegistry`, a public multi-key payload decryption helper for
  key-rotation windows, replay tooling, migration checks, and incident-response
  verification without adding cloud secret-manager SDKs to the core package.
- Added ADR 0005 documenting the AckTerm and AckNext evaluation, including the
  decision to keep AckNext out of production sink processing and to allow
  optional AckTerm only after successful DLQ publication.
- Added `dead_letter.ack_term_after_publish`, a disabled-by-default terminal
  acknowledgement policy that sends JetStream `AckTerm` only after successful
  DLQ publication for permanent failures.
- Added terminal acknowledgement metrics for opt-in DLQ terminal handling:
  `messages_terminated_total`, `message_term_seconds`, and
  `term_errors_total`.
- Added aggregate event freshness and staleness metrics for event age at receive
  time and durable store time, stale-event counts, missing or malformed creation
  timestamps, future timestamp counts, and positive source clock skew. The
  metrics remain observational only and are available through the local metrics
  snapshot, `nats-sink-metrics`, Prometheus policy allow lists, and OTLP policy
  allow lists without changing ACK behavior.
- Added read-only Oracle lineage query helpers and the `nats-sink query-lineage`
  command for bounded, redacted inspection of persisted events by allow-listed
  mission metadata fields, message ID, or subject. The helper uses bind
  variables for lookup values, validates configured tables and columns, omits
  payload output by default, and does not affect sink writes or ACK behavior.
- Added Oracle MySQL sink research and converted the result into the
  production sink implementation tracked by issue #101.
- Added a fail-closed pre-sink policy gate that runs after normalization,
  message metadata, mission metadata, and optional payload encryption but before
  any destination write. The gate supports subject-scoped requirements for
  priority, classification, labels, mission metadata, encrypted payloads,
  approved mission metadata root keys, and bounded sink-bound payload size.
- Added policy rejection handling that keeps rejected messages away from sinks,
  follows DLQ-before-ACK behavior for permanent validation failures, and avoids
  acknowledging originals when DLQ publication fails.
- Added pre-sink policy metrics, public policy helper exports, configuration
  validation, commit-then-ACK contract coverage, and operator documentation.
- Added optional core `size_policy` controls for sink-bound payload bytes,
  normalized headers, labels, mission metadata, standard metadata, approximate
  record size, and accepted batch size, with permanent-failure DLQ-before-ACK
  handling, aggregate metrics, tests, and operator documentation.
- Added an observability connector evaluation matrix and shared connector
  contract, then split the broad additional-observability roadmap item into
  individual connector backlog items for StatsD, Datadog, Splunk HEC, Elastic,
  Grafana Alloy, OCI Monitoring, Amazon CloudWatch, Azure Monitor, and syslog.
- Added a headers-only JetStream delivery evaluation and split implementation
  work into separate backlog items for consumer configuration,
  payload-presence metadata, and sink or DLQ certification.
- Added an optional confirmed ACK evaluation and split future implementation
  work into separate backlog items for confirmed ACK after sink success,
  confirmed DLQ acknowledgement behavior, and ACK confirmation metrics with an
  operator runbook.
- Added an optional InProgress evaluation and split future implementation work
  into separate backlog items for AckWait or BackOff guardrails, runtime
  InProgress heartbeats during long sink writes, and InProgress metrics with
  an operator runbook.
- Added an ordered-consumer evaluation and split future implementation work
  into separate backlog items for client compatibility checks, a read-only
  ordered inspection CLI, and durable replay-to-sinks guidance that keeps
  production writes on durable pull consumers.
- Added a push-consumer evaluation and split future implementation work into
  separate backlog items for capability and configuration guardrails, an
  opt-in bounded push runner mode, and push delivery-contract certification
  tests while keeping pull consumers as the production default.
- Added a subject-aware observability evaluation and split future
  implementation work into separate backlog items for a disabled-by-default
  policy model, bounded subject-family aggregation, and certification tests
  while keeping current metric export aggregate-only by default.
- Added a WebSocket connection evaluation and split future implementation work
  into separate backlog items for WebSocket configuration guardrails, optional
  connection header support, and an integration certification harness while
  keeping `nats://` and `tls://` as the certified production transports today.
- Added WebSocket NATS transport guardrails for `ws://` and `wss://`, including
  fail-closed mixed transport rejection, rejection of credentials embedded in
  URLs, `wss://` TLS context construction with local CA support, and unchanged
  commit-then-ACK processing.
- Added optional WebSocket connection header configuration through
  `nats.websocket_headers` and `nats.websocket_headers_env`, with bounded
  header validation, environment-sourced sensitive values, protocol-owned
  header rejection, redacted effective configuration, and `nats-py`
  `ws_connection_headers` option construction.
- Added a collision-safe local WebSocket certification harness and
  `scripts/run-websocket-e2e.sh`, which starts only its own temporary
  loopback `nats-server`, chooses free alternative ports when defaults are
  occupied, publishes synthetic messages over WebSocket, writes them through
  `FileSink`, and verifies the runner's ACK-after-sink-success path.
- Added optional tamper-evident custody metadata, including core configuration,
  deterministic payload and metadata hash helpers, optional previous-record
  hash capture, runner fail-closed behavior before sink writes, file sink
  record output, Oracle `METADATA_JSON.custody` persistence, public API
  exports, tests, and documentation.
- Added disabled-by-default JetStream advisory observation for selected
  `$JS.EVENT.ADVISORY...` subjects, including validated advisory configuration,
  bounded JSON parsing, low-cardinality advisory counters, runner lifecycle
  isolation from sink ACK behavior, tests, and operator documentation.
- Added an offline `nats-sink stream-plan` helper for JetStream stream
  management planning, including retention, discard, storage, replicas,
  duplicate-window, runtime permission, administration permission, NATS CLI
  example, and JSON output guidance without connecting to NATS or mutating
  stream state.
- Added explicit durable pull-consumer management with `bind_only`,
  `create_if_missing`, and `reconcile` modes, including safe startup drift
  validation for filter subject, explicit ACK policy, pull-consumer shape,
  AckWait, MaxDeliver, MaxAckPending, MaxWaiting, headers-only state, tests,
  and least-privilege permission documentation.
- Added richer durable pull-consumer policy configuration for multiple
  `FilterSubjects`, server-side `BackOff`, consumer replicas, memory-storage
  state, and bounded low-sensitivity JetStream consumer metadata, with
  fail-closed validation for unsafe combinations before message processing
  starts.
- Added optional `nats.no_echo` support, passed to `nats-py` as `no_echo`,
  with default-off behavior, environment override support, tests, and
  documentation that explains when same-connection echo suppression is useful.
- Added a disabled-by-default OpenTelemetry OTLP metrics connector under
  `nats-sink-observe otlp-export`, including validated policy fields,
  allow-list filtering, OTLP/HTTP JSON rendering, bounded request size,
  timeout and retry controls, environment-sourced headers, sanitized CLI
  output, unit coverage, public API exports, documentation, and systemd
  service/timer examples.
- Added detailed backlog items for future generic multi-sink routing and
  fan-out, including route matching by subject and metadata, named sink
  instances, optional ACK-gating wait policy, partial-failure metrics, and
  routing certification tests.
- Added `nats_sinks.spool.SpoolSink`, a first-party encrypted edge
  spool-and-forward sink for disconnected operation, including bounded local
  custody, record-level AES encryption, deterministic idempotency-key files,
  priority-aware replay, the `nats-sink replay-spool` command, unit coverage,
  example configuration, and operator documentation.
- Added managed backlog items for missing Oracle-family, cloud, streaming,
  lakehouse, database, messaging, and compatibility-profile connector
  candidates.

### Changed

- Refined the README and introductory architecture documentation to describe
  Oracle Database and OCI-hosted Oracle Autonomous Database as natural durable
  destinations while keeping the framework positioned for multiple sinks.
- Grouped Prometheus integration, metrics snapshot guidance, and NATS server
  monitoring under the Observability documentation section so external
  monitoring connectors are presented as sub-pages of the observability model.
- Reorganized the MkDocs navigation into a broader tree with sections for
  start-here material, core concepts, NATS, sinks, data handling,
  observability, deployment, security and supply chain, testing and quality,
  use cases, project workflow, and ADRs.
- Documented payload encryption key rotation, multi-key decryption, and
  secret-manager bootstrap patterns while keeping automated key rotation and
  provider-specific secret-manager integrations as future optional extensions.
- Moved the Postgres sink proposal out of active roadmap phases into
  "Not Planned Unless Scope Changes" and marked its backlog item as a
  low-priority, not-planned reference.
- Updated the NATS feature-gap analysis and roadmap so terminal
  acknowledgement work is represented as a narrow future DLQ-after-success
  feature instead of an open-ended AckTerm/AckNext evaluation.
- Expanded the WebSocket certification backlog requirements so the future
  local test harness must detect occupied NATS ports, select free loopback
  alternatives, and avoid interfering with unrelated running NATS processes.
- Expanded Oracle duplicate visibility so `nats-sink-metrics` can report
  merge rows, update-enabled merge rows with unknown insert-versus-match
  outcome, and no-update merge duplicates left unchanged after commit.

### Fixed

- Fixed a set of Oracle MySQL sink hardening gaps found during focused
  bug-hunt testing: conflicting password sources, blank password values,
  malformed password environment names, empty resolved password variables,
  connection-field control characters, empty TLS path strings, invalid pool
  names, duplicate or dotted column mappings, unknown or duplicate idempotency
  key columns, over-qualified table identifiers, max-length table DDL
  constraint naming, startup error classification for missing secrets,
  connection-pool cleanup after schema creation failure, and cleanup errors
  masking committed writes or permanent schema errors.
- Fixed Oracle MySQL sink connection-pool startup by normalizing positive
  fractional `connection_timeout` values to integer seconds before passing
  options to Oracle MySQL Connector/Python, with a regression test and
  container-backed e2e verification.
- Fixed Oracle MySQL SQL-builder static-analysis evidence by placing reviewed
  Bandit B608 suppressions on the exact validated SQL f-string line reported
  by the scanner, with regression coverage for the annotation placement.
- Fixed the local Docker smoke runner so expected transient NATS startup
  connection failures use a quiet error callback and NATS stream seeding
  failures are reported as concise `SmokeTestError` messages instead of raw
  tracebacks.
- Fixed pull request label synchronization to apply labels through
  `gh issue edit` against the pull request number instead of `gh pr edit`,
  avoiding an unrelated GitHub CLI GraphQL `projectCards` failure observed
  during live PR creation.
- Fixed pull request label source detection so Markdown inline code spans and
  fenced code blocks do not turn instructional placeholders such as
  `Related #123` into real source issues.
- Fixed pull request label source detection so ordinary body references such
  as `See #123` are not treated as source issues; only branch names, explicit
  `--issue` arguments, and dedicated `Related #123` lines are used.
- Fixed `scripts/open-release-pr.sh --issue` so explicit issue numbers are
  rendered into a `Related Issues` section in the pull request body, keeping
  issue linkage visible even when it cannot be inferred from the branch name.
- Fixed pull request label sync dry-run output so it reports "Would copy"
  instead of implying labels were already copied.
- Fixed malformed GitHub CLI JSON handling in pull request label sync so the
  helper raises a controlled workflow error instead of leaking a raw JSON
  decoding traceback.
- Fixed stale project-managed pull request labels so old release, severity,
  sink, lifecycle, and workflow labels are removed when the source issue no
  longer carries them, while manual reviewer labels are preserved.
- Fixed metadata trust-boundary validation so message metadata headers,
  configured priority/classification defaults, configured labels, mission
  metadata profile allow lists, and security-label vocabularies reject ASCII
  control characters consistently.
- Fixed ambiguous configured label handling by rejecting semicolons inside
  individual JSON array label items while preserving documented
  semicolon-separated string shorthand.
- Fixed security-label normalization so scalar fields and list items fail
  closed on non-string values instead of silently coercing JSON numbers or
  booleans into policy text.
- Fixed `NatsEnvelope` header normalization so malformed empty or
  control-character-bearing header names are dropped before sink storage.
- Fixed epoch nanosecond conversion to use exact integer arithmetic rather than
  floating-point timestamp multiplication.
- Fixed the new WebSocket harness unit tests so they mock loopback port probes
  rather than binding real sockets, preserving the no-network-unit-tests rule
  for locked-down CI and developer sandboxes.
- Fixed GitHub backlog relationship sync so native issue dependencies submit
  numeric `issue_id` values to the GitHub API instead of string values.
- Fixed GitHub backlog and bug priority sync so native Issue Priority field
  updates use the current GitHub Issue Field Values API payload shape.
- Fixed the Dependency Review workflow by moving
  `actions/dependency-review-action` to the Node.js 24-compatible `v5` release
  line so pull request dependency review no longer emits Node.js 20 action
  runtime deprecation warnings.
- Fixed the high-confidence secret scanner so it prefers `rg` when available
  but falls back to `grep` in minimal CI environments where ripgrep is not
  installed.
- Updated the PyPI version badge URL to use a shorter Shields.io cache period
  so README and documentation badges refresh more quickly after releases.
- Fixed the mission-support documentation discoverability regression test so it
  validates the current tree-shaped MkDocs navigation instead of the previous
  flat navigation entry.

## [0.4.0] - 2026-05-22

### Added

- Added a production secure-development baseline to `ROBOTS.md`, `AGENTS.md`,
  and the public security documentation, covering hostile-input handling,
  least privilege, fail-closed defaults, defense in depth, threat modeling,
  injection prevention, bounded resources, safe logging, dependency hygiene,
  file safety, deserialization safety, and testing expectations.
- Added unit coverage for log-control-character sanitization, strict log-level
  validation, duplicate JSON configuration keys, null configuration roots, and
  oversized configuration files.
- Added a dependency-free high-confidence secret scan script and wired it into
  `scripts/security.sh`, CI, and pre-commit.
- Added `docs/security-rule-review.md`, a 316-control review that maps the
  maintainer-provided secure-development guidance to the current codebase,
  test suite, documentation, non-applicable surfaces, and roadmap follow-up
  items.
- Added project-specific security controls covering documented public imports,
  release-version consistency, PyPI-safe documentation links, generated site
  output, sink capability checks, and security-register maintenance.
- Added a version-consistency check that compares `pyproject.toml`,
  `nats_sinks.__version__`, README release text, the documentation home page,
  and `CHANGELOG.md`.
- Added `scripts/check-docs.sh` so release, CI, and local documentation checks
  build Read the Docs and GitHub Pages variants in isolated temporary output
  directories instead of allowing overlapping MkDocs builds to collide in the
  shared `site/` directory.
- Added public API compatibility tests for the documented core, Oracle, and
  file sink import paths.
- Expanded public API compatibility testing into an explicit contract for
  package exports, sink extension points, documented configuration helpers, and
  `nats-sink` / `nats-sink-metrics` console-script entry points.
- Added public API compatibility documentation explaining the supported import
  surface, what the tests protect, and how future sinks should be added without
  breaking existing users.
- Added clearer basic metrics names for fetched, prepared, written, ACKed,
  NAKed, failed, DLQ, sink write, normalization error, encryption error, DLQ
  publish error, ACK error, last-success, and active-batch observations.
- Added `JsonFileMetrics`, a dependency-free local JSON metrics snapshot
  recorder that writes atomically for service scripts, local diagnostics, and
  the standalone metrics CLI.
- Added the separate `nats-sink-metrics` CLI with table, JSON, JSONL, shell,
  metric-name, and Prometheus text output, plus `show`, `get`, and `describe`
  commands for operator and developer workflows.
- Added public Python helpers for reading and flattening metrics snapshots:
  `load_metrics_snapshot`, `metric_rows_from_snapshot`, and
  `write_metrics_snapshot`.
- Added metrics contract tests and runner metrics tests proving telemetry
  increments without changing commit-then-ACK behavior.
- Added unit coverage for metrics snapshot validation, duplicate-key rejection,
  metrics CLI output formats, missing-metric handling, and stale snapshot exit
  behavior.
- Added clearer Oracle operator-facing error messages and tests for common
  schema, privilege, and authentication failures, including stale or
  incorrectly constructed retained e2e tables.
- Added NATS reconnect tuning fields, multiple seed URL support, and runner
  connection event metrics for disconnect, reconnect, close,
  discovered-server, and asynchronous error callbacks.
- Added tests proving NATS connection event metrics are recorded while
  preserving user-provided `nats-py` callbacks.
- Added release-ready documentation for NATS reconnect tuning, multiple seed
  URLs, and connection event metrics so operators can understand the supported
  configuration fields, metric names, and failure-observation behavior before
  enabling the feature in production.
- Added least-privilege NATS permission templates for sink runtime workers,
  DLQ-enabled deployments, optional runtime consumer creation, and separate
  advisory reader accounts.
- Added security, configuration, operations, DLQ, README, and roadmap links to
  the new NATS permission guidance so authorization planning is discoverable
  alongside authentication and TLS documentation.
- Added a NATS server monitoring endpoint design decision documenting that the
  delivery worker must not poll `/jsz`, `/healthz`, or other server monitoring
  endpoints, and that any future helper should be a separate
  disabled-by-default observability connector.
- Added mission-support operational example documentation for restricted event
  storage, disconnected file handoff, DLQ triage and replay preparation, and
  destination outage recovery, including configuration guidance, operational
  flow diagrams, failure behavior, sink-specific choices, and test guidance.
- Added the disabled-by-default NATS server monitoring observability connector
  under `nats-sink-observe`, with explicit endpoint allow lists, field allow
  lists, TLS verification controls, local CA support, bounded timeouts, bounded
  response size, sanitized JSON snapshots, and optional Prometheus text output
  for selected numeric values.
- Added unit tests for NATS monitoring policy validation, unsafe endpoint
  rejection, malformed JSON handling, sanitized snapshot generation, Prometheus
  rendering, and CLI behavior without making live network calls.
- Added Debian and Oracle Linux systemd assets plus installer support for the
  optional NATS monitoring snapshot service and timer, kept disabled until
  policy and service enablement are reviewed.
- Added advanced JetStream topology guidance covering mirrors, sources,
  subject transforms, republish behavior, stream compression, placement, stream
  metadata, unsupported management boundaries, and idempotency review questions.
- Added exponential, linear, and fixed retry backoff controls with optional
  full or equal jitter for delayed NAK handling after retryable failures.
- Added tests proving retryable failures use delivery-attempt-aware backoff
  delays, support deterministic no-jitter operation, and stop issuing active
  NAKs when the configured retry budget is exhausted.
- Added optional priority-aware processing lanes for already-fetched bounded
  batches, including validated lane configuration, weighted starvation
  controls, fail-closed handling for unsafe priority metadata, aggregate
  priority-lane metrics, commit-then-ACK tests, and dedicated documentation
  that explains ordering limitations.
- Added non-JSON boundary regression coverage for NATS authentication
  ambiguity, NATS URL scheme validation, TLS seed URL handling, direct
  `RetryPolicy` construction, Oracle `payload_field` idempotency, and
  negative JetStream metadata normalization.
- Added GitHub issue planning synchronization for managed bugs and backlog
  items, including required live GitHub Issue `Priority` field updates and
  native GitHub issue dependency relationships for declared `blocked_by` and
  `blocks` links.
- Added CycloneDX SBOM generation through `scripts/sbom.sh`, producing JSON and
  XML release-evidence artifacts under `dist/sbom/`.
- Added CI and release workflow steps that generate SBOM files after package
  build, upload them as workflow artifacts, and attach them to GitHub Releases
  without uploading them to PyPI.
- Added SBOM documentation covering local generation, automated release
  integration, security notes, limitations, and how operators can use SBOMs in
  vulnerability and compliance workflows.
- Added Oracle duplicate/conflict metrics for idempotent Oracle operations:
  `oracle_conflicts_total`, `oracle_duplicates_total`, and
  `oracle_duplicate_ignored_total`.
- Added tests and documentation showing how Oracle duplicate/conflict counters
  appear through the `nats-sink-metrics` CLI in table, shell, and Python
  snapshot-reading workflows.
- Added rich metrics documentation covering configuration, snapshot shape,
  shell scripting, Prometheus textfile output, Python hooks, exit codes,
  security guidance, and the metric reference.
- Added an observability core with disabled-by-default sharing policies,
  subject discovery from runtime config, allow/deny metric controls, and a
  future connector extension point separate from core delivery and sinks.
- Added the `nats-sink-observe` CLI for generating Prometheus observability
  policies, validating policies, listing available metric names, listing
  subject hints, and rendering policy-filtered Prometheus textfile output.
- Added a Prometheus textfile connector for node_exporter that reads only local
  metrics snapshots, exports no metrics unless explicitly enabled by policy,
  and avoids payloads, secrets, subjects, labels, classification values, table
  names, file paths, and high-cardinality labels by default.
- Added an optional native Prometheus HTTP scrape endpoint as a separate
  disabled-by-default observability connector that reads local metrics
  snapshots, applies the same allow-list policy as the textfile connector,
  enforces response-size and stale-snapshot controls, and avoids coupling
  endpoint failures to JetStream ACK behavior.
- Added Debian and Oracle Linux systemd assets for running the Prometheus
  textfile export as a separate oneshot service and timer from the main
  `nats-sink` worker.
- Added a disabled native Prometheus HTTP systemd service example and unified
  installer support so operators can run the scrape endpoint as a separate
  Linux service after explicit policy review.
- Added Kubernetes deployment examples with JSON ConfigMaps, Secret references,
  mounted trust material, worker and observability separation, resource limits,
  security contexts, NetworkPolicy guidance, graceful shutdown settings, and
  optional Prometheus HTTP sidecar manifests.
- Added `scripts/install-systemd.sh`, a unified systemd installer that detects
  Debian-family systems or Oracle Linux from `/etc/os-release` and applies the
  correct package-manager and service-user setup.
- Added documented Debian and Oracle Linux one-command install examples that
  download `scripts/install-systemd.sh` from GitHub and run it with `sudo`,
  plus safer review-first guidance for sensitive production environments.
- Added public observability and Prometheus documentation with diagrams,
  policy examples, CLI examples, Linux service guidance, node_exporter
  integration notes, security guidance, and future connector candidates.
- Added a public backlog-management guide that defines GitHub Issues as the
  live backlog, `CHANGELOG.md` as shipped history, and detailed close-out
  expectations for feature requests.
- Added local JSON backlog staging under `backlog/items/`, a
  `scripts/sync-backlog-issues.py` GitHub CLI sync tool, and a `Backlog Sync`
  GitHub Actions workflow for idempotently creating or updating GitHub Issues
  from local backlog definitions.
- Added generated `requirements*.txt` dependency manifests derived from
  `pyproject.toml` so GitHub Dependency Graph and Dependabot have stable
  pip-compatible manifests for runtime and optional dependency groups.
- Added `scripts/update-dependency-manifests.py` plus CI, pre-commit, and
  local check integration to ensure generated dependency manifests stay in
  sync with package metadata.
- Added dependency-management documentation covering GitHub Dependency Graph
  enablement, generated manifest maintenance, Dependabot, dependency review,
  and supply-chain security boundaries.
- Added detailed local backlog JSON items for all currently unrealized Phase 2
  and Phase 3 roadmap work so the roadmap can be synchronized into GitHub
  Issues as actionable enhancement requests.
- Added `target_release` support to backlog JSON sync so issues receive
  `release-unscheduled` or concrete release labels before implementation work
  starts.
- Changed managed backlog and bug sync so priority is maintained through the
  official GitHub Issue `Priority` field instead of issue labels. The sync
  tools now remove legacy `priority-p*` labels from managed issues during
  update and support an explicit Issue field ID for automation tokens that can
  edit issues but should not enumerate organization Issue fields.
- Added managed issue workflow support for a `completed` label on bug reports
  and feature requests after local implementation evidence has been posted,
  keeping fixed or implemented issues open but clearly marked while they wait
  for release-gated closure.
- Added a managed bug-report workflow with local sanitized JSON staging under
  `bugs/reports/`, a `scripts/sync-bug-reports.py` GitHub CLI sync tool,
  severity and priority labels, default assignment to `louwersj`, and a
  dedicated `Bug Report Sync` GitHub Actions workflow.
- Added `scripts/comment-bug-issue.py` for test-driven bug lifecycle comments
  requiring failing-test evidence before fixes and regression, verification,
  and close-out evidence after fixes, including an optional sanitized
  `--test-file` attachment for small focused regression tests.
- Added release workflow integration through `scripts/close-released-bug-issues.py`
  so managed bug reports close only after the associated GitHub Release exists,
  acceptance criteria are checked, and sanitized fix evidence is present.
- Expanded the public bug report issue form, backlog-management guide,
  `ROBOTS.md`, and `AGENTS.md` with the required bug-report, TDD, evidence,
  release-label, and release-gated close-out workflow.
- Added public-safety validation to backlog sync and backlog comment tooling
  so local enhancement requests and implementation notes reject common leak
  patterns before they reach GitHub Issues.
- Added `scripts/comment-backlog-issue.py` for sanitized progress comments,
  release-label updates, and release-gated close-out comments that verify the
  GitHub Release before closing an enhancement request.
- Added `scripts/generate-checksums.py` and release workflow integration to
  attach a `SHA256SUMS` manifest for wheel, source distribution, and SBOM
  artifacts to GitHub Releases.
- Added [Hash-Verified Installs](docs/hash-verified-installs.md) guidance for
  pinned, hash-checked `pip --require-hashes` deployments in high-trust
  environments.
- Added release workflow automation that closes managed backlog issues labeled
  for a release only after the associated GitHub Release exists.
- Added stricter backlog issue lifecycle enforcement: start comments require
  planned work, test plan, and documentation/release-note sections; completion
  comments require completed work, acceptance criteria, test-plan evidence, and
  close-out evidence sections.
- Added backlog helper support for assigning issues, marking Acceptance
  Criteria checklist items complete, removing stale `release-unscheduled`
  labels when a concrete release label is applied, and preventing release
  automation from closing issues that lack checked acceptance criteria or
  close-out/test evidence.
- Added a detailed backlog item for a future native Oracle Cloud Infrastructure
  Object Storage sink, including functional requirements, non-functional
  requirements, security expectations, test planning, documentation scope, and
  release success criteria.
- Expanded the GitHub feature request issue form and pull request template so
  backlog items capture operational context, delivery semantics, security
  considerations, acceptance criteria, test plans, documentation plans, and
  close-out evidence.
- Added unit coverage for observability policy generation, policy validation,
  Prometheus allow/deny filtering, observation suppression, textfile writing,
  and the new observability CLI.
- Added explicit testing and Oracle documentation for retained e2e table schema
  drift, fresh current-schema test tables, and backend timing metrics as
  functional observations rather than production benchmarks.
- Added a deterministic synthetic mission scenario harness under
  `nats_sinks.testing`, plus `scripts/run-synthetic-harness.py`, for generating
  sanitized fake `NatsEnvelope` scenarios covering valid JSON, malformed
  JSON-like text, duplicates, stale timestamps, encrypted-payload markers,
  NATO-style classification values, priority values, labels, and empty
  payloads without requiring live NATS or Oracle services.
- Added file-sink synthetic harness coverage and documentation so maintainers
  can run local smoke scenarios with uncompressed or gzip-compressed file
  output while keeping generated files under ignored local paths by default.
- Added use-case documentation pages for defence and mission-support patterns,
  including synthetic mission testing guidance that keeps domain-specific
  examples in documentation while preserving a generic sink framework.
- Added an F2T2EA event phase tagging blueprint that documents metadata-only
  lifecycle tagging, allowed example phase values, explicit non-goals, Oracle
  mission metadata JSON column examples, file sink record examples, and
  sanitized tracked JSON examples for validation.
- Added broader defence and mission-support blueprint pages for sensor event
  custody, classification and labels, chain of custody, cross-domain handoff
  preparation, edge operation, and audit-oriented persistence. The pages
  explain current generic nats-sinks features without making the product
  defence-only or implying targeting, fire-control, weapons-release, or
  autonomous decision behavior.
- Added a generic mission metadata profile that can resolve one validated JSON
  context object from a NATS header, global defaults, or subject-aware defaults
  before a message reaches any sink.
- Added `NatsEnvelope.mission_metadata` and
  `mission_metadata_for_json_storage()` so future sinks can preserve the same
  validated context without depending on Oracle- or file-specific behavior.
- Added Oracle `MISSION_METADATA_JSON` mapping and recommended table DDL so
  richer mission, operation, platform, source-system, track, confidence,
  releasability, or lifecycle metadata can be stored without adding fixed
  columns for every profile field.
- Added file sink output support for top-level `mission_metadata` and
  `metadata.mission_metadata`.
- Added unit tests for mission metadata parsing, duplicate-key rejection,
  subject-aware defaults, profile allow-lists, size limits, secret-like key
  rejection, file sink output, Oracle row mapping, and DLQ-before-ACK handling
  for invalid metadata.
- Added deterministic bounded property-style generator tests for subject
  matching, payload normalization, message metadata normalization, mission
  metadata validation, and file path sanitization without adding a new
  dependency.
- Added Oracle benchmark tooling with `scripts/run-oracle-benchmark.sh` and
  `scripts/run-oracle-benchmark.py`, reporting publish, fetch, map, Oracle
  execute, Oracle commit, ACK, retry-delay, and shutdown timing as sanitized
  environment-specific observations.
- Added benchmark report helpers under `nats_sinks.testing` so report
  redaction, phase aggregation, and command validation are covered by unit
  tests without live NATS or Oracle services.
- Added sanitized synthetic load-test profiles with
  `scripts/run-load-profile.py` and `scripts/run-load-profile.sh`, covering
  normal, retry, DLQ, shutdown, optional encryption-workload, and
  metrics-snapshot behavior without live services.

### Changed

- Standardized SPDX source headers across Python and shell files with
  `SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>` and
  `SPDX-License-Identifier: Apache-2.0`.
- Hardened JSON configuration loading so config files are size-bounded,
  duplicate-key checked, UTF-8 checked, and required to use a JSON object at
  the root.
- Hardened CLI logging setup so unknown log levels fail closed and log messages
  escape control characters before reaching terminals or log collectors.
- Wired release-version consistency checking into the local check script, CI,
  and pre-commit.
- Kept legacy metrics aliases such as `batch_write_seconds` and
  `messages_received_total` while documenting the clearer preferred names such
  as `sink_batch_write_seconds` and `messages_prepared_total`.
- Extended `metrics` configuration with `snapshot_file` so `nats-sink run` can
  write a local JSON snapshot when metrics are enabled.
- Updated Debian and Oracle Linux install scripts to install disabled
  observability policy examples and Prometheus textfile systemd units without
  enabling external sharing by default.
- Changed the older distribution-specific systemd install scripts into
  compatibility wrappers that delegate to the unified
  `scripts/install-systemd.sh` installer.
- Changed the unified systemd installer so it can run from a git checkout or
  as a standalone downloaded script by fetching required example config and
  systemd unit files from GitHub using `NATS_SINKS_INSTALL_REF`.
- Changed standalone systemd installs so tagged installer runs default to the
  matching PyPI package version, with `NATS_SINKS_PACKAGE_SPEC` available for
  optional extras such as `nats-sinks[oracle]`.
- Expanded public mission-oriented wording to explicitly describe
  sensor-driven warfighting support contexts such as sensor-fusion,
  command-and-control, sensor-to-shooter, kill-chain, and kill-mesh data
  flows, while clearly stating that `nats-sinks` is not targeting,
  fire-control, weapons-release, or lethal decision-making software.
- Extended `delivery` configuration with `retry_backoff_max_ms`,
  `retry_backoff_mode`, `retry_backoff_multiplier`, and `retry_jitter`.
- Added `cyclonedx-bom` to development dependencies because SBOM generation is
  a build and release evidence task, not a runtime dependency.

### Fixed

- Fixed NATS configuration validation so primary and seed URLs fail closed
  unless they use supported NATS client schemes: `nats`, `tls`, `ws`, or
  `wss`.
- Fixed NATS authentication validation so token, username/password,
  credentials-file, and NKEY seed-file modes are mutually exclusive, and
  username/password mode requires both a username and exactly one password
  source.
- Fixed CLI NATS option construction so a TLS context is created when any
  configured seed URL uses `tls://`, not only when the fallback primary URL
  uses TLS.
- Fixed direct `RetryPolicy` construction so invalid negative values, unknown
  runtime modes, non-finite multipliers, and impossible caps are rejected
  consistently with JSON configuration validation.
- Fixed exponential retry backoff so extreme delivery attempts return the
  configured cap instead of raising before jitter or delayed NAK handling.
- Fixed Oracle `payload_field` idempotency validation so empty path segments
  and control characters are rejected during configuration validation.
- Fixed Oracle `payload_field` idempotency extraction so objects and arrays are
  rejected as ambiguous keys instead of being converted to language-specific
  string representations.
- Fixed NATS consumer normalization so negative JetStream sequence, delivery,
  and pending metadata values are treated as absent rather than persisted in
  envelopes.
- Fixed runtime package version drift by aligning `nats_sinks.__version__` with
  the `0.3.0` package metadata.
- Fixed managed bug-report test attachments so shell scripts, Python files,
  JSON, Markdown, TOML, YAML, and plain-text files render with matching
  Markdown code fences instead of always using a Python fence.
- Fixed file sink path sanitization so a hostile or unusual value whose string
  conversion fails produces a bounded fallback filename component instead of an
  unexpected sanitizer exception.
- Fixed synthetic load-profile phase-rate reporting so shutdown, DLQ,
  backend-write, ACK, retry, and encryption phase throughput uses the
  phase-specific completed-work counter instead of the total generated message
  count.
- Fixed Oracle benchmark report interpretation so retry-delay and shutdown
  phases are timing-only observations and no longer report misleading
  messages-per-second values.
- Fixed payload JSON parsing so Python-only constants such as `NaN` and
  `Infinity` are not treated as valid JSON. `json_only` mode now raises a
  serialization error, while `json_or_envelope` preserves the original text in
  the payload envelope.
- Fixed payload JSON parsing so duplicate object keys are treated as
  ambiguous. `json_only` mode now fails closed, while `json_or_envelope`
  preserves the original body as text instead of silently keeping only the last
  duplicate value.
- Fixed metrics snapshots so non-finite metric values are rejected before
  local JSON snapshot writing or loading can produce non-standard JSON.
- Fixed the metrics CLI description path so strict type checking and Ruff
  validation pass under the release CI matrix.
- Fixed the release workflow artifact layout so PyPI publishing receives only
  wheel and source distribution files. `SHA256SUMS` remains release evidence
  and is attached to the GitHub Release instead of being uploaded to PyPI.
- Fixed the optional NATS server monitoring connector so endpoint responses and
  stored snapshots reject non-standard JSON constants before observability
  output is generated.
- Fixed the optional NATS server monitoring connector so duplicate endpoint
  response keys are rejected before allow-listed fields are extracted.
- Fixed strict JSON handling across configuration loading, backlog and
  bug-report sync manifests, mission metadata headers, encryption envelopes,
  observability policy writing, Oracle benchmark reports, and synthetic
  load-profile reports. These paths now reject duplicate keys, non-standard
  constants, or non-finite timing values before public evidence or sink-facing
  data can be generated.

## [0.3.0] - 2026-05-20

This release is the next feature release after `0.2.1`. The main themes are
safer payload handling and richer message context that is resolved once in the
core runtime and then persisted consistently by every production sink.

Highlights:

- Payload encryption can now be enabled before sink delivery. The core runner
  encrypts only the NATS message body with AES-256-GCM or AES-256-CCM, leaving
  operational metadata available for routing, idempotency, observability, and
  troubleshooting. Operators can enable one global policy for all subjects or
  ordered per-subject rules for selective encryption and exemptions.
- Every message can now carry normalized `priority`, `classification`, and
  `labels` metadata. Values can come from configurable NATS headers,
  deployment defaults, ordered subject-specific defaults, or remain null/empty
  when neither is provided.
- Oracle storage now includes dedicated `PRIORITY`, `CLASSIFICATION`, and `LABELS`
  columns in the recommended table shape.
- File sink JSON output now includes top-level `priority`, `classification`,
  `labels`, and `labels_list` fields as well as the same values in the generic
  metadata document.
- Mermaid diagrams now render from the same Markdown source for Read the Docs
  and GitHub Pages.
- Public documentation now uses more mission-oriented wording where relevant,
  with examples for defence logistics, operational reporting, sensitive
  payload handling, audit trails, DLQ triage, and disconnected handoff patterns.

Upgrade notes:

- Existing Oracle tables must be migrated before using the `0.3.0` Oracle
  default column mapping. Add nullable `PRIORITY`,
  `CLASSIFICATION`, and `LABELS` columns, or configure
  `sink.columns.priority`, `sink.columns.classification`, and
  `sink.columns.labels` to match existing columns.
- If an older retained Oracle integration or e2e test table is reused, the
  test will fail fast with a schema message. Use a fresh table or the
  documented drop-before-test flag for test-only tables.
- Payload encryption requires installing the optional crypto extra:
  `pip install "nats-sinks[crypto]"`.
- When payload encryption is enabled, prefer metadata-based idempotency such as
  JetStream stream sequence or message ID. Do not depend on plaintext payload
  fields after the core encrypts the message body.

Validation snapshot:

- Full local check script passed with `164 passed, 8 skipped`.
- Encryption-focused check passed with `68 passed`.
- Sink capability check passed with `66 passed`.
- Live NATS-to-Oracle e2e passed for both unencrypted and encrypted modes
  against fresh retained test tables that include the new `LABELS` column.
  The runs verified priority/classification/labels persistence, encrypted
  payload storage, decrypt verification, and commit-then-ACK completion.

### Added

- Added optional core payload encryption before sink delivery, with
  AES-256-GCM and AES-256-CCM support through the `nats-sinks[crypto]` extra.
- Added encrypted payload envelope helpers and public Python imports for
  `EncryptionConfig`, `EncryptionRuleConfig`, `PayloadEncryptor`,
  `SubjectPayloadEncryptor`, and `decrypt_payload`.
- Added subject-specific payload encryption rules with NATS wildcard matching,
  first-match-wins behavior, disabled-rule exemptions, inherited global
  encryption settings, and dedicated unit plus local file e2e coverage.
- Added encryption coverage for core runner ordering, file sink storage,
  gzip-plus-encryption file output, Oracle row mapping, local file e2e, and
  live Oracle e2e opt-in mode.
- Added `scripts/check-encryption.sh` for temporary test key generation and
  encryption-focused validation, with a `--preserve-key-material` debug flag.
- Added core-normalized `priority`, `classification`, and `labels` message
  metadata fields with configurable NATS header extraction, defaults, file sink
  persistence, Oracle columns, and unit/e2e coverage across present and missing
  values.
- Added subject-specific priority, classification, and labels defaults under
  `message_metadata.rules`, using NATS wildcard matching and first-match-wins
  resolution while preserving header values as authoritative.
- Added encrypted file sink example configuration under
  `examples/payload-encryption/`.
- Added `scripts/check-gh-auth.sh` so maintainers can validate local GitHub CLI
  authentication, and optionally start interactive browser login, before
  pushing release tags.
- Documented the GitHub CLI authentication preflight in the release and
  publishing runbooks.
- Documented payload encryption configuration, subject-specific encryption
  rules, encrypted envelope shape, decryption helpers, key handling,
  idempotency guidance, and encrypted Oracle/file sink behavior.
- Documented priority/classification/labels message metadata configuration,
  subject-specific defaults, semicolon-separated label storage, null/empty
  handling, Oracle schema impact, file sink output shape, and test coverage.
- Added concrete documentation examples showing how encrypted payloads,
  NATO-style classification values, priority, and semicolon-separated labels
  appear in file sink JSON records and Oracle table rows.
- Added public PyPI and supported-Python-version badges to the README and
  documentation home page, with publishing guidance for future badge updates.
- Expanded `ROBOTS.md` and `AGENTS.md` with payload-encryption,
  priority/classification/labels metadata, live Oracle e2e, retained
  test-table, and Oracle JSON-column handling guidance for future maintainers
  and AI agents.

### Changed

- Extended the recommended Oracle table DDL with nullable `PRIORITY`,
  `CLASSIFICATION`, and `LABELS` columns.
- Extended the generic metadata snapshot with a `message_metadata` object that
  contains normalized `priority`, `classification`, and `labels` values.
- Extended file sink output records with top-level `priority` and
  `classification`, `labels`, and `labels_list` fields.
- Updated the example file and Oracle configurations to show the new
  `message_metadata` section.
- Updated the sanitized test report with the latest local and live e2e
  validation results.
- Refined README and documentation wording so public readers in operational,
  public-sector, and defence-adjacent environments can more easily map the
  generic sink framework to mission event streams and secure data-handling
  practices.
- Updated example JSON configurations to demonstrate NATO-style classification
  strings, priority defaults, and labels alongside encryption and sink storage
  behavior.

### Fixed

- Enabled Mermaid fenced-code rendering in MkDocs so Read the Docs and GitHub
  Pages can render diagrams from the same Markdown source.

## [0.2.1] - 2026-05-19

### Fixed

- Adjusted the file sink health-check unit test so it avoids direct
  `pathlib.Path.rglob()` calls inside async test code, matching Ruff's
  async-safety checks in CI.

### Added

- Added optional gzip compression for the file sink, including compressed
  multi-file test coverage and documentation.
- Added file sink e2e test controls for retaining or deleting generated local
  files, defaulting to delete-after-test behavior.
- Expanded the configuration documentation so core runtime settings, file sink
  settings, and Oracle sink settings list defaults, valid values, validation
  rules, and production guidance in one place.

### Changed

- Reordered the README and documentation home page so current production
  capabilities, including Oracle and file sinks, are introduced before future
  roadmap items.
- Added GitHub Pages documentation publishing workflow and maintainer
  documentation for enabling Pages as a hosted documentation mirror.
- Added GitHub Pages links to the README, documentation home page, release
  guide, development guide, and package project URLs.
- Added GitHub Pages MkDocs builds to local, CI, docs, and release validation
  paths so the Pages mirror is checked before future publication.
- Clarified that file sink gzip compression uses Python's standard-library
  `gzip` module and does not depend on an operating-system gzip command.

## [0.2.0] - 2026-05-18

### Added

- Added `nats_sinks.file.FileSink` as the second production sink.
- Added local file sink JSON configuration with deterministic filenames,
  atomic temporary-file placement, optional fsync, subject partitioning,
  payload normalization, metadata persistence, and duplicate policies.
- Added CLI registry support for `sink.type: "file"`.
- Added the tracked `examples/file-basic/config.json` local file sink example.
- Added unit coverage for file sink mapping, duplicate handling, path
  sanitization, payload wrapping, health checks, and filesystem error
  classification.
- Added deterministic local end-to-end coverage proving the core runner writes
  through `FileSink` before ACKing messages.
- Added `scripts/check-sinks.sh` and CI/release workflow sink capability checks
  so production sink behavior is validated before publication.
- Added Read the Docs build configuration, a GitHub Actions documentation
  workflow, and version-local documentation linking so hosted docs can build
  automatically after the one-time Read the Docs project import.
- Added dedicated file sink documentation covering configuration, durability,
  idempotency, duplicate policies, payload handling, filesystem safety,
  throughput notes, and production recommendations.

### Changed

- Updated the release workflow artifact upload action to a Node.js 24-compatible
  GitHub Action version so release jobs do not emit Node.js 20 deprecation
  warnings.
- Clarified agent and release guidance so documentation and `CHANGELOG.md` stay
  prepared for the next release throughout normal development.
- Updated README, configuration, getting started, testing, release, publishing,
  security, operations, performance, Python usage, sink framework, and roadmap
  documentation for the Oracle-plus-file-sink project shape.
- Updated package metadata for version `0.2.0` and Read the Docs project URLs.

## [0.1.1] - 2026-05-18

### Fixed

- Replaced relative Markdown documentation links with fully qualified GitHub
  URLs so the PyPI-rendered project description links to repository
  documentation correctly.
- Fixed the advertised `nats-sink --version` option so it exits successfully
  before requiring a subcommand.

### Added

- Added `scripts/check-markdown-links.py` to prevent future PyPI README link
  regressions.
- Added the Markdown link check to local check scripts and CI.
- Documented PyPI README link hygiene in the publishing runbook.
- Added a deterministic unit test for the global CLI version option.

## [0.1.0] - 2026-05-18

### Added

- Initial core JetStream sink runner with commit-then-acknowledge processing.
- Immutable `NatsEnvelope` model and sink protocol.
- Oracle sink with idempotent `merge` and `insert_ignore` modes.
- Oracle subject-to-table routing with ordered NATS wildcard patterns.
- Oracle Autonomous Database connection options for walletless TLS and
  wallet/mTLS, including wallet directory and wallet password environment
  support.
- Oracle sink sessions disable parallel DML by default to keep transactional
  multi-row batches reliable on Autonomous Database services such as `high`.
- CLI commands for run, validate, effective config, and sink testing.
- Documentation, examples, CI skeletons, and open-source governance files.
- Service deployment examples and installer scripts for Debian and Oracle Linux.
- Message sizing guidance and Oracle DDL using `CLOB` for subject storage.
- NATS token and password environment-variable support for connection secrets.
- NATS connection documentation covering token auth, username/password auth,
  server-side bcrypt password storage, and TLS with local CA certificates.
- Tracked manual live NATS probe script and example docs for connection,
  subscription, and publish-and-receive validation without committing secrets.
- Environment-gated Oracle integration tests that create the test table when
  missing, write rows, and verify duplicate redelivery is idempotent.
- Environment-gated live NATS-to-Oracle end-to-end integration test that
  publishes configurable JetStream message counts, runs `JetStreamSinkRunner`,
  stores rows in Oracle, and verifies the ACK path. The default e2e message
  count is 256.
- E2E test timing support for backend write duration through
  `batch_write_seconds`, plus wildcard subscription coverage via separate
  subscribe and publish subjects.
- Performance documentation covering Oracle write tuning, batch sizing,
  Autonomous Database behavior, and future staging-table optimization work.
- Unhappy-path hardening and deterministic fuzz-style unit coverage for message
  normalization, subject route validation, Oracle identifier validation, and
  unexpected sink exceptions.
- Shared payload normalization for JSON-capable sinks, including Oracle support
  for valid JSON, non-JSON UTF-8 text, encrypted-text-style payloads, and
  base64-wrapped bytes through `payload_mode`.
- Oracle and live NATS-to-Oracle e2e tests now cover mixed JSON and non-JSON
  text payload persistence.
- Generic NATS metadata snapshots with all headers, known and future `Nats-*`
  reserved headers, JetStream sequence metadata, and epoch nanosecond timing
  fields for message-created, received, and stored times.
- Oracle recommended schema and row mapping now include `METADATA_JSON` plus
  epoch timing columns, with integration tests covering missing `Nats-Msg-Id`
  and present `Nats-Expected-Stream` headers.
- Oracle integration and live e2e tests now use retained named test tables by
  default, with explicit opt-in `DROP_TABLE_BEFORE` and `DROP_TABLE_AFTER`
  flags plus a tracked `scripts/run-oracle-e2e.sh` helper.
- Empty NATS message bodies are covered by unit, Oracle integration, and live
  NATS-to-Oracle e2e tests.
- Added `docs/test-report.md` as the single latest sanitized validation report
  for core framework, Oracle sink, package, documentation, and live e2e checks.
- Added explicit partial-batch coverage proving that `batch_size` is an upper
  bound and that final smaller batches are written, committed, and ACKed.
- Updated `scripts/run-oracle-e2e.sh` so command-line table, message-count, and
  batch-size overrides take precedence over sourced `.local` environment files.
- Agent guidance for rigorous failure-path testing, secure coding, public code
  documentation, and graceful processing-loop behavior.
- Oracle least-privilege account documentation covering owner/runtime user
  separation, required grants, and privileges the sink service must not have.
- NATS feature gap analysis comparing current project scope with broader NATS
  connection, JetStream, stream, consumer, observability, and data abstraction
  capabilities.
- Roadmap expanded with planned NATS compatibility work and intentionally
  out-of-scope NATS capabilities.
- Release workflow now creates or updates a GitHub Release for pushed `v*`
  tags after PyPI publishing and attaches the built source distribution and
  wheel.

### Changed

- Runtime configuration uses JSON files instead of YAML files.
- Generic sink framework documentation is split from Oracle-specific documentation.
- Generic README, architecture, configuration, idempotency, message sizing, and
  performance documentation now describe the framework boundary first and link
  to Oracle-specific details from `docs/oracle-sink.md`.
- Added explicit guidance for introducing future sink modules as additive,
  non-breaking releases through the existing `NatsEnvelope`, `Sink` protocol,
  registry, optional extras, and sink-specific JSON configuration fields.
- Added a public logging level reference covering `DEBUG`, `INFO`, `WARNING`,
  `ERROR`, `CRITICAL`, runtime overrides, and payload logging guidance.
- Roadmap now tracks certified TLS certificate authentication, NKEY challenge
  authentication, and decentralized JWT authentication/authorization support.
