# Instructions for AI Agents

This repository is safety-sensitive infrastructure code. Follow these rules:

- Never ACK a JetStream message before durable sink success.
- Never introduce a sink that can silently lose messages.
- Never log secrets, credentials, tokens, full connection strings, private keys, or sensitive payloads by default.
- Never log encryption keys, generated key material, plaintext payloads, or
  decrypted payloads by default.
- Never weaken idempotency behavior without updating tests and documentation.
- Never add dependencies without a clear reason.
- When dependencies change, edit `pyproject.toml` first, regenerate the
  generated `requirements*.txt` manifests with
  `python scripts/update-dependency-manifests.py`, and keep the manifest check
  green so GitHub Dependency Graph and Dependabot see the intended dependency
  surface.
- Never make network calls in unit tests.
- Keep unit tests deterministic.
- Keep integration tests isolated behind markers.
- Use deterministic bounded property-style tests for validators, parsers,
  normalizers, subject matching, metadata handling, and file path sanitization
  when they accept external or semi-trusted input. If adding a property-testing
  dependency such as Hypothesis, justify it as development-only and update
  dependency manifests, docs, and release notes.
- Keep only the latest sanitized test report at `docs/test-report.md`; never
  commit raw logs, server addresses, usernames, passwords, tokens, certificate
  contents, wallet material, connection strings, or sensitive payloads.
- Treat malformed payloads, invalid commands, invalid configuration, network failures, database failures, and DLQ failures as first-class production paths.
- Add deterministic unhappy-path and fuzz-style tests for validators, parsers, normalizers, and delivery decisions.
- Prefer small, reviewable changes.
- Never commit ordinary work directly to `main`. Use the hierarchical branch
  model: `release-vX.Y.Z` from `main`, issue or feature branches from the
  release branch, and bug branches from the active issue or feature branch
  when defects are found during development.
- Merge bug branches back into their issue or feature branch after the failing
  test, fix, and sanitized evidence are complete. Merge issue or feature
  branches back into the release branch after implementation evidence,
  documentation, changelog updates, and local checks are complete. Merge the
  release branch into `main` only when the maintainer explicitly decides to
  release.
- Keep ordinary branch pushes quiet. Do not start GitHub Actions after every
  small branch commit. Use `scripts/open-release-pr.sh --base <target-branch>`
  to create or refresh a draft pull request against the correct hierarchy
  target, then run `scripts/run-release-validation.sh` only when the branch is
  ready for merge or release validation.
- For issue, feature, and bug pull requests raised by this workflow,
  `scripts/open-release-pr.sh --ready` auto-approves ready non-main PRs by
  default. Use `--no-auto-approve-non-main` only when manual inspection is
  needed before approval. The helper must refuse any pull request whose base
  branch is `main`, should verify the expected PR author when possible, and
  must never replace tests, evidence, documentation updates, or release
  approval. Release pull requests into `main` remain manual.
- The `Branch Pull Request` workflow is manual and token-gated. Do not
  re-enable push-triggered pull request creation unless the maintainer
  explicitly changes the release policy.
- Release tags must point at commits already merged into `main`; do not tag
  unmerged work branches.
- Keep `main` protected through GitHub branch protection. Require pull
  requests, CODEOWNER review, stale-review dismissal, resolved conversations,
  and the supported Python CI matrix before merge. Use
  `scripts/apply-branch-protection.sh` when the repository policy needs to be
  applied or repaired.
- Treat GitHub Issues as the live backlog and `CHANGELOG.md` as the shipped
  history. User-visible feature work should have a detailed GitHub feature
  request before implementation unless the change is a small typo or
  mechanical maintenance item.
- If a new backlog item is found locally and GitHub access is unavailable,
  create a validated JSON item under `backlog/items/` and run
  `python scripts/sync-backlog-issues.py --dry-run`; do not treat chat history
  as the backlog.
- Backlog JSON must include a clear `target_release` value. Use
  `unscheduled` until the work is assigned to a concrete release tag, then use
  a tag such as `v0.4.0` so the issue receives a release label.
- Backlog and bug JSON must include one of the supported priority values. Let
  the sync tooling write the official GitHub Issue `Priority` field. Do not
  hand-write priority labels, recreate legacy `priority-p*` labels on issues,
  or bypass the Issue-field sync path for normal backlog and bug work.
- When an issue depends on, blocks, or is otherwise related to another managed
  issue, declare that relationship in the local JSON `relationships` object
  using only safe references such as `#123`, `backlog:item-id`, or
  `bug:item-id`. Let the tooling populate GitHub-native dependency
  relationships where supported. Never include private URLs, IP addresses,
  system names, payloads, or secrets in relationship metadata.
- Before live issue sync, ensure `gh` can update issues
  (`gh auth refresh -s repo -s read:org`) and set
  `NATS_SINKS_GITHUB_ISSUE_FIELD_ORG` and
  `NATS_SINKS_GITHUB_ISSUE_PRIORITY_FIELD` if the defaults are not correct.
  In automation, prefer the optional
  `NATS_SINKS_GITHUB_ISSUE_PRIORITY_FIELD_ID` when a token can edit issues but
  should not enumerate organization Issue fields.
- Never put secrets, credential values, certificate material, private
  operational details, live network locators, IP addresses, sensitive subjects,
  or payload examples in backlog JSON, GitHub feature requests, progress
  comments, close-out comments, or generated issue bodies.
- Validate local backlog definitions with
  `python scripts/sync-backlog-issues.py --check` and dry-run them before
  syncing. The validation must reject common public-leak patterns before
  content reaches GitHub Issues.
- When implementing a feature request, link the issue in the pull request and
  add a sanitized progress comment with
  `python scripts/comment-backlog-issue.py --dry-run` before posting it for
  real. The comment should explain the intended implementation approach,
  tests, documentation, and planned release tag without leaking private
  details.
- Before starting issue work, assign the GitHub issue to the maintainer doing
  the work, apply the concrete release label, and post a sanitized `started`
  lifecycle comment. The comment must include `Planned Work`, `Test Plan`, and
  `Documentation And Release Notes` sections.
- Before editing code for a managed issue, create or switch to the issue branch
  from the active release branch. If a bug is discovered inside that feature
  branch, create a separate bug report, branch from the feature branch, add the
  failing test first, and merge the bug branch back into the feature branch
  after verification.
- When implementation is complete locally, post a sanitized `completed` or
  `closeout` lifecycle comment. The comment must include `Completed Work`,
  `Acceptance Criteria`, `Test Plan Evidence`, and `Close-Out Evidence`
  sections. Use `--complete-acceptance` only after the test plan has actually
  been executed and evidence has been summarized without secrets or private
  operational details. The managed comment helper applies the `completed`
  label for `completed`, `closeout`, and `released` lifecycle statuses. This
  label means implementation is complete in development while the issue
  remains open until release-gated closure.
- Do not use pull request closing keywords for normal feature work before a
  release is published. Prefer `Related #123`; the release workflow closes
  managed backlog issues after the associated GitHub Release exists.
- Close a feature request only after the release that contains it has actually
  been published. Use the release-gated close-out helper where practical so the
  tool verifies the GitHub Release before closing the issue. Release
  automation must also verify that acceptance criteria are checked and that
  close-out evidence plus test-plan evidence comments exist.
- When closing a feature request, include a detailed close-out summary covering
  what shipped, which checks passed, which docs changed, and any known
  limitations or follow-up issues.
- Treat defects found during testing, review, or release preparation as
  managed bug reports, not informal chat notes. Create or sync a sanitized
  bug report under `bugs/reports/*.json` with `python scripts/sync-bug-reports.py`
  unless the defect is a tiny non-user-visible typo.
- Bug reports must be assigned to `louwersj`, carry the official `bug` label,
  a severity label, a priority label, and a release label. Never include
  secrets, live service locators, IP addresses, credentials, certificate
  material, wallet data, sensitive subjects, or payload examples in bug JSON,
  bug comments, or issue bodies.
- Fix bugs with test-driven development. Add the smallest focused failing
  regression test first, add it to the normal test suite, then post a
  sanitized `failing-test` comment with `scripts/comment-bug-issue.py --dry-run`
  before implementing the fix.
- For bug fixes, post a sanitized `started` comment before or during the fix
  and a `completed` or `closeout` comment after verification. Completed bug
  comments must include `Completed Fix`, `Acceptance Criteria`,
  `Regression Test Evidence`, `Verification Evidence`, and `Close-Out
  Evidence`. The managed bug comment helper applies the `completed` label for
  `completed`, `closeout`, and `released` lifecycle statuses so fixed bugs can
  stay visibly complete while waiting for the release that closes them.
- Keep managed bug reports open until the release containing the fix is
  published. Release automation closes only bug issues with the matching
  release label, checked acceptance criteria, and sanitized regression,
  verification, and close-out evidence.
- Update docs when public behavior changes.
- Update `CHANGELOG.md` for user-visible changes.
- Keep documentation and `CHANGELOG.md` prepared for the next release after
  every change. The `Unreleased` section should describe user-visible work
  before a release branch, tag, or package build is started.
- Maintain public API stability.
- Keep security, typing, linting, packaging, and tests green.
- Treat `commit-then-acknowledge` as a non-negotiable invariant.
- Document public code and add comments around safety-sensitive or non-obvious logic.

The goal of every change is to keep `nats-sinks` boring in production: clear
interfaces, predictable failures, secure defaults, well-tested behavior, and no
surprising side effects during import, startup, shutdown, or message handling.

Architecture rule:

> Core owns delivery semantics. Sinks own destination writes.

Destination sinks must not receive raw NATS messages and must never call ACK, NAK, TERM, or any other JetStream acknowledgement method.

Core-owned transformations, including payload encryption and message metadata
resolution, must happen before `sink.write_batch(...)`. Sinks should store the
normalized metadata and normalized or encrypted payload they receive and should
not implement parallel first-layer encryption, decryption, priority parsing,
classification parsing, or ACK logic unless the sink is explicitly designed and
documented as a trusted transformation sink.

Subject-specific core policies, such as payload encryption rules, must use the
shared NATS subject matcher and must be evaluated deterministically in
configuration order. First matching rule wins, unmatched subjects use the
documented global fallback, and disabled rules are explicit exemptions.

Retry behavior is delivery semantics. Preserve bounded retries, exponential
backoff, jitter controls, and the rule that retry exhaustion must not ACK a
message whose durable work did not succeed.

Required processing order:

1. Receive the message.
2. Validate that the message can be processed.
3. Execute business logic.
4. Persist or commit durable state.
5. ACK only after successful completion.

Prefer safe duplication over silent loss.

## Production Python Standards

Use modern Python practices that make the package dependable for external
users, downstream maintainers, and automated operations teams:

- Keep imports side-effect free. Importing `nats_sinks` must not open network
  connections, read secrets, start background tasks, mutate global process
  state, or configure application-wide logging unexpectedly.
- Keep public APIs small, typed, documented, and stable. If a public symbol,
  config field, CLI option, or documented behavior changes, update tests,
  documentation, and `CHANGELOG.md`.
- Prefer explicit dependency injection for NATS clients, sinks, clocks, retry
  policies, and metrics hooks so tests can use mocks without network calls.
- Use `dataclasses`, `Protocol`, and Pydantic models intentionally. Data
  structures should communicate whether they are immutable runtime values,
  validated configuration, or replaceable interfaces.
- Preserve exception chaining with `raise ... from exc` when translating driver
  or library exceptions into framework-defined errors.
- Avoid broad mutable module state. If state is needed, keep it instance-local,
  protected by lifecycle methods, and safe to initialize and close more than
  once.
- Treat async code carefully. Do not call blocking network, database, file, or
  subprocess operations from the event loop unless they are explicitly isolated
  or documented as safe.
- Bound memory use. Any buffer, queue, cache, retry loop, or batch collector
  must have clear limits and predictable shutdown behavior.
- Close resources deterministically. NATS connections, Oracle pools, cursors,
  tasks, and metrics exporters must be closed in normal shutdown and failure
  paths.
- Do not add runtime dependencies unless they materially improve reliability,
  security, maintainability, or user experience. Prefer the standard library
  when it is sufficient.
- Avoid dynamic imports from untrusted configuration. Sink selection should go
  through safe registries or explicit entry points with documented behavior.
- Keep generated artifacts out of source control unless they are intentionally
  tracked examples or documentation assets. Treat `site/` as MkDocs output:
  edit README, `docs/`, `mkdocs.yml`, and source configuration instead of
  editing generated HTML as the source of truth.
- Keep runtime version reporting aligned with release metadata. If
  `pyproject.toml` changes, `src/nats_sinks/__init__.py`, README release text,
  `docs/index.md`, and `CHANGELOG.md` must be updated together and
  `scripts/check-version-consistency.py` must pass.
- Treat documented imports as compatibility promises. Public import paths shown
  in the README or docs need public API tests before they are changed, removed,
  or moved.

## Configuration And Secrets

Configuration is part of the public contract. Treat it with the same care as
code:

- Validate configuration at the boundary and fail with actionable
  `ConfigurationError` messages.
- Prefer environment-variable references for secrets, such as password
  environment variable names, instead of storing secret values in config files.
- Prefer `encryption.key_b64_env` for payload encryption key material. Direct
  `encryption.key_b64` values are for disposable local tests only and must be
  redacted from output.
- When payload encryption key rotation is touched, preserve the encrypted
  payload envelope schema, keep `key_id` non-secret but operationally bland,
  and test old-key plus new-key decryption through `PayloadKeyRegistry`.
- Do not add cloud secret-manager SDKs to the core package for encryption
  unless the dependency is isolated behind a deliberate optional extra and a
  documented provider-specific connector.
- When adding or changing `encryption.rules`, document whether rules inherit
  global key material, override key material, or disable encryption for matching
  subjects. Rule order is security-sensitive and must be clear in examples.
- Missing encryption key material must fail gracefully with a framework
  `ConfigurationError` or clean CLI runtime error before any message is ACKed.
  Do not let missing key variables become uncaught tracebacks in normal CLI
  operation.
- Message metadata defaults such as `message_metadata.priority.default` and
  `message_metadata.classification.default` are configuration, not secrets.
  Still document them clearly because they can affect routing, storage,
  incident triage, and downstream policy decisions.
- Subject-specific defaults under `message_metadata.rules` are also
  configuration. Header values remain authoritative; subject defaults apply
  only when the corresponding header is absent. Test first-match-wins behavior,
  unmatched fallback, explicit null defaults, and empty headers whenever rules
  change.
- Redact secrets in CLI output, logs, exceptions, reports, and test snapshots.
- Do not dump complete process environments, complete connection strings, or
  raw headers that may contain credentials.
- Observability policy files are security policy. Generated policies must be
  disabled by default, must not export payloads or secrets, and must require
  explicit allow lists before a connector shares metrics with Prometheus or any
  future platform.
- Prometheus textfile export is a separate service concern. Keep
  `nats-sink-observe` independent from NATS, Oracle, file sinks, and future
  destinations; it should read local snapshots, apply policy, and write only
  approved metrics.
- NATS server monitoring endpoint integration must remain a separate
  observability concern. Do not make `JetStreamSinkRunner` poll `/jsz`,
  `/healthz`, or any other monitoring endpoint. Any NATS monitoring connector
  must be disabled by default, validate URLs and endpoint paths, enforce
  timeouts and response-size limits, extract only allow-listed scalar fields,
  avoid storing the monitoring base URL in snapshots, and never affect ACK,
  retry, DLQ, or sink-write behavior.
- Do not add sensitive or high-cardinality labels to observability connectors
  by default. Subjects, message IDs, stream sequence values, table names, file
  paths, priority values, classification values, labels, payload fields,
  usernames, and host-specific secrets require explicit design review before
  they can be exported anywhere.
- Subject-aware observability must stay disabled by default until a reviewed
  policy model, bounded subject-family aggregation, and certification tests are
  in place. Prefer stable operator-approved family labels over raw subject
  labels, enforce cardinality caps, and remember that hashing a subject is not
  the same as making it non-sensitive.
- Keep example credentials obviously fake and clearly marked for local
  development only.
- When adding config fields, document defaults, accepted values, security
  impact, and whether changing the value can affect delivery guarantees.

## Security Engineering

Assume `nats-sinks` will be used in critical production systems:

- Treat every external input as hostile until it has been validated,
  normalized, bounded, authorized where relevant, and safely converted into a
  typed internal structure. NATS messages, headers, subjects, JSON config,
  environment variables, filesystem paths, database rows, DLQ messages,
  command-line arguments, and third-party library responses are all trust
  boundaries.
- Prefer fail-closed behavior. Authentication, authorization, validation,
  configuration loading, dependency loading, encryption setup, sink registry
  selection, and policy evaluation must deny the operation or raise a
  framework error when the safe decision is ambiguous.
- Apply defense in depth. Validation, allow-listing, type checks, bounded
  resource use, safe logging, dependency scanning, integration-test gates,
  least-privilege examples, and runtime shutdown behavior should overlap so
  one missed layer does not create silent loss or data exposure.
- Threat-model important features before implementation. Identify the assets,
  trust boundaries, abuse cases, attacker capabilities, operational impact,
  worst-case failure mode, and tests that prove the design fails safely.
- Keep security designs simple, explicit, and reviewable. Avoid clever hidden
  behavior, implicit dynamic dispatch, user-controlled code paths, and
  surprising fallback behavior.
- Centralize security-sensitive logic. Authentication option construction,
  TLS context creation, configuration parsing, SQL identifier validation,
  payload encryption, metadata extraction, redaction, DLQ shaping, ACK
  decisions, and log sanitization should live in one obvious place with tests.
- Secure behavior must be the default. Risky behavior such as direct secrets
  in JSON config, payload logging, TLS verification disablement,
  non-idempotent append-style writes, retained test key material, or destructive
  test-table cleanup must require explicit configuration and documentation.
- Treat internal systems as potentially hostile. Compromised publishers,
  poisoned queues, corrupted caches, malicious insiders, and stale retained
  test databases can bypass perimeter assumptions.
- Document invariants directly in code, tests, documentation, ADRs, and release
  notes. Future maintainers must know that commit-then-ACK, idempotency,
  redaction, bounded input, payload encryption semantics, and metadata handling
  are safety properties, not optional style preferences.
- Treat message payloads, NATS headers, Oracle rows, and DLQ messages as
  potentially sensitive.
- Treat encryption key material as highly sensitive. Tests that generate keys
  must clean them up by default and preserve them only behind explicit local
  debug flags.
- Payload encryption protects the message body only. Metadata such as NATS
  subject, headers, stream names, sequence numbers, message IDs, timestamps,
  priority, classification, labels, route names, file paths, and table names
  remains clear unless a future feature explicitly documents otherwise. Do not
  claim metadata confidentiality from payload encryption.
- Subject-specific encryption does not hide the subject used for matching.
  Do not put secrets in NATS subject names. If subject families have different
  data-classification requirements, test matching, non-matching, and exemption
  rules for every production sink affected by the change.
- Treat `classification` values as potentially sensitive operational metadata.
  They may reveal information-handling policy even when payloads are encrypted,
  so do not print them in broad debug dumps or high-cardinality metrics labels
  without a deliberate product decision and documentation.
- Encrypted payload envelopes may include non-secret operational fields such as
  algorithm, key ID, nonce, ciphertext, plaintext size, and plaintext digest.
  Treat those envelopes as sensitive destination data even though they do not
  contain plaintext.
- Use allow-list validation for SQL identifiers, sink names, route names, and
  file paths supplied by configuration.
- Use bind variables for database values. Never concatenate user-provided
  values into SQL statements.
- Keep TLS verification enabled by default. Any local-development exception
  must be explicit, documented, and unsuitable for copy-paste production use.
- Prefer least privilege in examples and documentation. Production Oracle users
  should receive only the permissions needed for the configured sink table.
- Do not introduce pickle, unsafe YAML loading, shell interpolation, or unsafe
  deserialization of untrusted data.
- Validate input at every boundary with allow lists for accepted values,
  formats, lengths, ranges, URL schemes, file extensions, MIME types, enum
  fields, SQL identifiers, sink names, route names, NATS subjects, filenames,
  and operational modes. Reject malformed input early instead of repairing,
  guessing, or partially accepting it.
- Normalize and canonicalize paths, URLs, encodings, Unicode text, hostnames,
  and filenames before validation or comparison. Filesystem writes must resolve
  to an intended base directory and reject traversal, symlink escape, absolute
  extracted paths, and ambiguous names.
- Enforce maximum sizes for config files, JSON payloads, strings, arrays,
  batch operations, retries, queue depths, file output, decompressed data, and
  any count that could allocate memory or trigger downstream native behavior.
- Use real parsers for structured input. Do not parse nested or escaped formats
  with ad hoc string splitting or regular expressions. Reject duplicate or
  ambiguous keys in security-sensitive JSON where parser differences could
  change meaning.
- Keep data and code separate in SQL, shell commands, HTML, JavaScript, XML,
  LDAP, regular expressions, templates, serializers, and browser contexts. Do
  not concatenate untrusted data into executable or interpretable strings.
- Use parameterized SQL for values and allow-list validation for dynamic table
  names, column names, operators, sort fields, route names, and sink names.
- Avoid shell execution in production code. If a subprocess is ever required,
  use a fixed executable, argument-list calls with `shell=False`, timeouts, a
  minimal environment, output limits, and sanitized output.
- Treat logs as an injection surface. Sanitize control characters, newlines,
  terminal escape sequences, and attacker-controlled formatting before log
  records reach terminals or collectors.
- Treat Python native extensions, C libraries, compression libraries, database
  drivers, image/archive parsers, and FFI boundaries as memory-unsafe
  components. Validate sizes, offsets, counts, file headers, decompressed
  sizes, and dimensions before handing untrusted data to native code.
- Use the `secrets` module or cryptographic library randomness for keys,
  nonces, tokens, and identifiers with security impact. Never use `random` for
  security-sensitive randomness.
- Use constant-time comparison for secrets, MACs, signatures, reset tokens, or
  future authentication material.
- Keep cryptographic code on established libraries and authenticated modes.
  Never invent custom encryption, signing, hashing, key exchange, or nonce
  handling. Keys must stay separate from encrypted data and support versioned
  rotation in production designs.
- Keep secrets out of source code, Git history, logs, screenshots, tickets,
  test fixtures, generated docs, Docker images, command-line arguments, and
  client-visible assets. Prefer short-lived, least-privileged, auditable
  credentials over long-lived personal tokens.
- Never deserialize untrusted data with object-capable formats such as
  `pickle`, `marshal`, `shelve`, unsafe YAML loaders, or equivalent formats
  that can instantiate classes or execute hooks.
- For file handling, generate server-side names, bound file counts and sizes,
  use secure temporary files, reject traversal, validate archive paths before
  extraction, and treat Markdown, SVG, HTML, documents, media, fonts, and
  archives as active or risky content unless sanitized or sandboxed.
- Avoid SSRF-style features by default. If future sinks or tools fetch
  user-supplied URLs, allow-list schemes, hosts, ports, and destination
  services; block private, loopback, link-local, multicast, and metadata
  ranges after DNS resolution and redirects; set strict timeouts and response
  size limits; and never forward internal credentials.
- Use atomic database constraints, transactions, compare-and-swap behavior,
  unique indexes, and destination idempotency keys instead of relying on
  check-then-act logic in memory.
- Bound retries and external calls. Use timeouts, finite retry counts,
  backoff, jitter, backpressure, admission control, and graceful degradation.
- Keep dependencies pinned or constrained, scanned, justified, actively
  maintained, and reviewed for new transitive risk. Do not install
  dependencies dynamically at runtime.
- Avoid `eval`, `exec`, unsafe dynamic imports, user-controlled attribute
  access, runtime code generation, broad monkey-patching, mutable default
  arguments, and hidden global state.
- Handle errors explicitly. Catch only exceptions that can be handled safely;
  unexpected errors should fail through controlled framework paths without
  leaking stack traces, SQL fragments, filesystem paths, environment variables,
  internal hostnames, or secrets to users.
- When adding file handling, avoid path traversal, avoid following untrusted
  symlinks for sensitive files, and document expected permissions.
- Keep dependency updates, CodeQL, dependency review, Ruff, typing, Bandit, and
  package checks green.
- Keep GitHub Dependency Graph support healthy. The generated pip manifests are
  release and security evidence, not hand-maintained dependency sources.
- Treat SBOM generation as release evidence. Keep `scripts/sbom.sh`, local
  check scripts, CI, release workflows, release documentation, and
  `CHANGELOG.md` aligned whenever package build or dependency behavior changes.
- Never include secrets, payloads, live service details, local wallet files,
  certificates, private keys, or `.local/` runtime configuration in SBOM
  artifacts or SBOM documentation. SBOM files should be derived from package
  metadata and the build environment only.
- Keep `docs/security-rule-review.md` current when the security posture
  changes. If a new sink, protocol surface, authentication mode, parser,
  filesystem behavior, crypto behavior, web/API feature, native dependency, or
  release process changes an applicability decision, update the register,
  tests, documentation, agent guidance, and changelog in the same change.

## Production Hardening Checklist

Before completing any code change, evaluate the change against this checklist
and update code, tests, documentation, and `CHANGELOG.md` where the answer is
not clearly safe:

- external input is validated, normalized, bounded, and rejected by default
  when invalid;
- least privilege applies to users, database accounts, containers, CI jobs,
  cloud identities, runtime service accounts, and file permissions;
- authentication and authorization, if present, are separated and deny by
  default when identity, tenant, role, ownership, policy, or resource state is
  missing or ambiguous;
- object-level authorization is enforced for the exact subject, action,
  resource, tenant, and object state when a feature exposes objects;
- SQL, shell, HTML, JavaScript, XML, LDAP, regex, template, and serialization
  contexts keep code separate from data;
- secrets are not present in code, logs, traces, tests, images, command-line
  arguments, tickets, screenshots, or client-visible assets;
- deserialization cannot instantiate objects, import modules, call hooks, or
  execute code from untrusted input;
- file paths, uploads, archives, temporary files, and generated outputs cannot
  escape intended storage locations;
- subprocess calls, if unavoidable, use fixed executables, argument lists,
  `shell=False`, timeouts, minimal environments, and sanitized output;
- every external operation has a timeout, bounded retries, backoff, jitter, and
  idempotency where needed;
- caches, queues, payloads, parser depth, batch operations, and memory growth
  are bounded;
- logs are structured, sanitized, redacted, useful for operators, and free of
  sensitive payloads by default;
- dependencies are constrained, scanned, justified, actively maintained, and
  not dynamically installed at runtime;
- native-code and FFI boundaries validate sizes, lifetimes, file metadata, and
  untrusted input before crossing the boundary;
- performance-sensitive changes are measured before optimization and keep
  readability unless profiling proves otherwise;
- tests cover normal paths, failure paths, malformed input, abuse cases,
  boundary values, duplicate messages, dependency failures, and concurrency
  risks where relevant;
- static analysis, linting, type checking, dependency scanning, secret
  scanning, formatting, package builds, and documentation builds remain green;
- graceful shutdown, health checks, monitoring signals, and safe deployment or
  rollback guidance are documented when runtime behavior changes;
- crashes, hangs, memory growth, data corruption, flaky behavior, and parser
  inconsistencies are treated as reliability and security signals.

## Failure Handling

Long-running sink processes must handle non-happy flows gracefully. A malformed
payload, bad route, transient NATS disconnection, Oracle outage, rejected DLQ
publish, unexpected client object, or unexpected sink exception must not create
an early ACK or an unclassified silent failure.

Use framework-defined errors for expected failure categories:

- `TemporarySinkError` for retryable downstream failures.
- `PermanentSinkError` for invalid messages or unrecoverable sink input.
- `SerializationError` and `ValidationError` for bad payloads or message shape.
- `DeadLetterError` when DLQ publication fails before the original ACK.
- `AckError` when durable success happened but JetStream ACK failed.

Boundary-level `except Exception` blocks are acceptable only when they prevent a
service loop from crashing, log safe context, and route the message to redelivery
or a documented error path. Do not catch broad exceptions inside business logic
to hide failures. Never suppress errors silently.

Every failure path should answer three questions in code, tests, and
documentation:

1. Was durable state committed?
2. Was the JetStream message acknowledged, not acknowledged, negatively
   acknowledged, or published to a DLQ?
3. What should an operator do next?

When durable success is uncertain, prefer redelivery. When a permanent failure
is certain and a DLQ is configured, publish to the DLQ first and ACK the
original message only after the DLQ publication succeeds.

## Observability And Logging

Production operations depend on useful, safe signals:

- Log lifecycle transitions, configuration validation failures, retry decisions,
  DLQ decisions, sink start/stop events, and summary counters.
- Keep logs structured enough for ingestion by common log systems, even when
  using the standard `logging` package.
- Never log payloads by default. If payload logging is explicitly enabled,
  document the risk and keep test coverage for redaction behavior.
- Include stable identifiers such as stream, consumer, subject, sequence, batch
  size, and sink type when they are safe to log.
- Avoid high-cardinality or sensitive values in metrics labels.
- Metrics and timing measurements should be best-effort observations; they must
  not affect ACK ordering or durable commit behavior.
- NATS server monitoring snapshots are observability evidence only. They must
  not include raw endpoint JSON by default, credentials, base URLs, private
  topology, subjects, account names, stream names, consumer names, payloads, or
  secrets unless a reviewed policy explicitly selects a safe scalar field.
- Oracle duplicate/conflict metrics are observability, not delivery semantics.
  They must stay low-cardinality, free of table names, subjects, constraint
  names, payloads, message IDs, classification values, labels, and secrets, and
  must never decide whether a JetStream message is ACKed.
- Metrics names are part of the operational contract. Add new metrics through
  `src/nats_sinks/core/metrics.py`, document them in operations guidance, keep
  labels low-cardinality, avoid sensitive metadata as labels, and preserve
  compatibility aliases when renaming existing operational signals.
- The `nats-sink-metrics` CLI must remain a local snapshot reader. It should
  not connect to NATS, Oracle, local file sink directories, cloud services, or
  future destination backends. Keep table, JSON, JSONL, shell, names, and
  Prometheus text output deterministic and easy to pipe in scripts.
- Metrics snapshots must stay bounded, schema-versioned, duplicate-key
  checked, UTF-8 checked, and free of payloads, secrets, credentials,
  certificate material, private key material, and sensitive operational content.

## Sink Connector Framework

- Treat every new destination as a sink connector with a documented durable
  success boundary, idempotency model, security model, and certification plan.
- Oracle Database and FileSink are first-party built-in connectors. Future
  Oracle-family sinks, such as OCI Object Storage, Oracle MySQL,
  Oracle Berkeley DB, Oracle NoSQL Database, and OCI Streaming, should also be
  first-party connectors in this repository unless governance explicitly
  changes that posture.
- External connector discovery is a code-execution and supply-chain boundary.
  Keep it disabled by default, require `plugins.allowed_sinks`, and never allow
  JSON configuration to specify arbitrary module paths, class paths, or dynamic
  imports.
- External connectors must expose a `SinkConnector` descriptor, match the
  allow-listed entry-point name, declare compatibility metadata, and pass
  certification tests before production recommendation.
- Do not mark a connector production-ready merely because it implements the
  Python protocol. Require tests for ACK-after-durable-success, no ACK on
  failure, duplicate redelivery, secret redaction, no payload logging by
  default, and destination-specific commit behavior.
- Palantir Foundry, Palantir Gotham, and other third-party platform connectors
  require local fake clients or contract harnesses before live certification is
  attempted. Never imply live certification from public documentation alone.

## Data And Idempotency

At-least-once delivery means duplicate processing is normal:

- New sinks must define their idempotency model before they are considered
  production-ready.
- Prefer natural durable keys such as JetStream stream plus stream sequence, or
  a validated message ID, over best-effort in-memory duplicate tracking.
- Document duplicate behavior for each write mode. Non-idempotent modes must be
  clearly marked and tested.
- Schema changes must be backwards-compatible where practical. If a migration is
  required, document the operational sequence and rollback considerations.
- Store metadata defensively. Missing optional NATS headers must not crash the
  sink, and future NATS headers should be preserved in metadata snapshots where
  the sink supports metadata persistence.
- Preserve the core `priority`, `classification`, and `labels` metadata
  contract for all production sinks. Values come from configured NATS headers,
  configured global defaults, configured subject-specific defaults, or remain
  null/empty. Do not store the literal string `"null"` for missing values.
- Apply metadata defaults only when the configured header is absent. If a
  configured priority, classification, or labels header is present but empty or
  whitespace-only, preserve that as explicit null or no labels for the message.
- Preserve the core `mission_metadata` contract for all production sinks that
  support structured metadata. Mission metadata is one validated JSON object,
  not a growing set of fixed domain-specific columns. Oracle stores it in
  `MISSION_METADATA_JSON`, file sink records expose it as top-level
  `mission_metadata`, and future sinks should document their equivalent
  storage behavior.
- Treat mission metadata as hostile input until it has been parsed with a JSON
  parser, duplicate-key checked, size-bounded, profile-checked when configured,
  and screened for secret-looking key names. Invalid mission metadata is a
  permanent validation failure and must follow DLQ-before-ACK behavior.
- Do not use `priority`, `classification`, or `labels` as idempotency keys
  unless a future sink explicitly documents a safe, unique, and tested use
  case. They are labels for operations and policy, not durable uniqueness
  guarantees.
- When payload encryption is enabled, use stable metadata-based idempotency
  keys. Do not rely on ciphertext hashes as duplicate keys because fresh
  nonces make ciphertext intentionally non-deterministic.
- Do not use payload-field idempotency for core-encrypted payloads unless a
  future feature explicitly supports trusted pre-encryption key extraction. Once
  the core encrypts the body, sinks cannot inspect original business JSON
  fields by design.
- If a destination stores encrypted payloads, tests should prove the stored
  encrypted envelope decrypts back to the exact original bytes for JSON, text,
  empty, and binary payload cases.

## Oracle And Live E2E Lessons

Live Oracle tests are useful precisely because they reveal driver and database
behavior that mocks can miss:

- Oracle JSON columns may be returned by `python-oracledb` as JSON text, LOB
  objects, dictionaries/lists, or mappings containing `Decimal` numeric values.
  Tests and diagnostics that read JSON columns must normalize those shapes
  deliberately instead of assuming a plain string.
- Retained test tables can outlive schema changes. Integration tests should
  verify required columns and fail fast with a clear message when a table has
  an older layout. Do not silently drop or recreate retained tables unless the
  operator set an explicit drop/recreate flag or selected a fresh test table.
- Oracle tables using the default mapping now require nullable `PRIORITY` and
  `CLASSIFICATION` and `LABELS` columns. When changing the recommended schema
  again, update the DDL helper, Oracle docs, least-privilege setup docs,
  retained-table schema checks, live e2e tests, and release notes together.
- Live encrypted and unencrypted e2e checks should use explicit test table
  names when validating new storage behavior, so old local environment defaults
  cannot hide schema drift.
- Timing printed by live e2e tests is functional evidence, not a benchmark.
  Document it as an observation and avoid drawing production throughput claims
  from small test runs.

## Testing Discipline

Every change that touches delivery, idempotency, configuration, parsing, NATS
connection options, SQL generation, or Oracle writes should include tests for
malformed input and failure behavior. Unit tests must be deterministic and must
not depend on NATS, Oracle, the network, or wall-clock timing. Integration tests
may use real services only when guarded by explicit markers and environment
variables.

Before committing production code, run the normal local checks when practical:

```bash
ruff format --check .
ruff check .
mypy src
python -m pytest -q
mkdocs build --strict
python -m build
scripts/sbom.sh
twine check dist/*.whl dist/*.tar.gz
```

Additional testing expectations:

- Add regression tests for every bug fix.
- Cover both success and failure behavior for public functions and CLI commands.
- Use table-driven tests for validators and route matching where possible.
- Use property-style or fuzz-style tests for parsers and normalizers when they
  accept external input.
- Keep bounded generator tests small enough that failing cases are actionable
  in CI logs and do not print sensitive payloads, credentials, local service
  locators, or raw private operational data.
- Keep live NATS, Oracle, and end-to-end tests behind explicit integration
  markers and environment variables.
- Encryption test helpers should generate temporary AES key material, delete it
  by default, and preserve it only through an explicit local flag. Never write
  generated keys into tracked examples, reports, screenshots, or docs.
- When a feature supports both encrypted and unencrypted operation, tests
  should cover both paths for every production sink where practical. At minimum,
  unit tests should cover each sink and live e2e tests should be opt-in and
  documented.
- For message metadata, test all meaningful combinations for each production
  sink where practical: priority, classification, and labels present; only one
  field present; none present; defaults applied; subject defaults applied; and
  explicitly empty headers becoming null or no labels.
- Prefer the synthetic mission scenario harness for repeatable edge-case
  evidence when live NATS or Oracle services are not required. Keep generated
  subjects, payloads, classifications, labels, and reports fake and sanitized.
  Do not add live service access to the harness itself; use separate
  integration wrappers gated by ignored local configuration.
- Sanitize test reports before committing. Do not include hostnames, usernames,
  passwords, wallet contents, certificates, tokens, or private payloads.
- Do not mark live integration tests as passed unless they were actually run in
  the current environment.

## Documentation And Comments

Source files should contain meaningful module documentation. Public classes and
functions should have docstrings explaining contracts, especially around ACK
ordering, durable commit boundaries, idempotency, redaction, and Oracle SQL
safety. Comments should explain the reason for safety-sensitive behavior rather
than restating obvious code.

Documentation should be written for external users who may be new to NATS,
JetStream, Oracle, Python packaging, or sink connectors:

- Explain concepts before relying on acronyms or internal terminology.
- Include runnable commands where possible, using fake credentials and
  clearly-marked local-development examples.
- Use diagrams and sequence diagrams for delivery, ACK, DLQ, and sink
  lifecycle behavior when they make the flow easier to understand.
- Keep generic framework documentation separate from sink-specific
  documentation so future sinks can be added without confusing Oracle-specific
  guidance with core behavior.
- Use mission, defence, public-sector, and operational wording carefully when
  it helps the intended audience understand the software. Keep the language
  subtle and precise; do not imply official status, accreditation, tactical
  suitability, exactly-once delivery, or security guarantees the project does
  not provide.
- Treat F2T2EA phase tagging and similar mission lifecycle concepts as
  metadata-only documentation patterns unless a separate generic feature has
  been explicitly designed, implemented, and tested. Never imply that
  nats-sinks performs targeting, fire-control, weapons release,
  rules-of-engagement evaluation, or autonomous decision-making.
- Update README, docs pages, examples, and CLI help together when public
  behavior changes.
- Keep the documentation set in a release-ready state. Do not leave new
  behavior documented only in code comments, chat notes, local files, or test
  output.

## Packaging And Release Hygiene

The package should remain PyPI-ready after ordinary development work:

- Keep `pyproject.toml` metadata, optional extras, console scripts, classifiers,
  Ruff configuration, mypy configuration, pytest configuration, and coverage
  configuration coherent.
- Keep `src/` layout and `py.typed` intact so type information is distributed to
  users.
- Ensure README links render correctly on PyPI. README documentation links
  should use fully qualified Read the Docs URLs, while source-code and
  repository links should use fully qualified GitHub URLs.
- Keep `docs/` links version-local with relative Markdown links unless the link
  intentionally points to an external site or repository source path.
- Do not publish from a dirty tree. Release commits should include version,
  changelog, documentation, and test-report updates that match the released
  behavior.
- Before tagging, the `Unreleased` changelog entries should be moved into the
  new version section with the release date, and the documentation should match
  the package version being published.
- Use short-lived authentication and trusted publishing where available. Do not
  commit PyPI tokens, GitHub tokens, signing keys, or local release credentials.
- Release automation should create or update the GitHub Release from the pushed
  tag and attach the built source distribution and wheel.
- Release automation should generate CycloneDX SBOM JSON and XML files after
  the package build, upload them as workflow artifacts, and attach them to the
  GitHub Release as evidence. Do not upload SBOM files to PyPI as package
  distributions.
- Keep GitHub Actions versions current with GitHub-hosted runner runtimes so
  releases do not depend on deprecated Node.js versions.
