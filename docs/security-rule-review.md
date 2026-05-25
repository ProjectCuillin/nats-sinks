# Security Rule Review

This page records the repository review against the 316 secure-development
guidance points provided by the maintainer. It is intended to be a living
control register for external readers, maintainers, and AI agents working on
`nats-sinks`.

The review is not a formal certification or accreditation artifact. It is a
practical engineering record: each control is mapped to the current codebase,
the current applicability decision, and the evidence or follow-up path.

## Review Method

The rules were evaluated against the current project shape:

- a Python package and CLI,
- JSON configuration loading,
- NATS JetStream consumption,
- Oracle and file sink implementations,
- payload encryption,
- message metadata,
- metrics snapshots and policy-controlled observability export,
- local and CI test automation,
- release and documentation workflows.

Several controls describe browser applications, web sessions, uploads, archive
extraction, native C/C++ code, password login systems, or server-side HTTP
fetching. Those surfaces do not currently exist in `nats-sinks`, so the correct
status is `Not applicable`. If a future HTTP sink, web UI, upload feature, or
native extension is added, those controls must be reopened before the feature
is merged.

## Status Terms

| Status | Meaning |
| --- | --- |
| `Applied` | A concrete code, test, documentation, or automation change was made during the hardening pass. |
| `Already covered` | The current repository already had a matching control or design pattern. |
| `Partially covered` | The project has some relevant safeguards but still has planned improvement work. |
| `Roadmap` | The control is relevant to future production maturity and should be tracked before `1.0.0` or before a matching feature lands. |
| `Not applicable` | The repository currently has no matching technical surface. |

## Evidence Legend

| Evidence | Meaning |
| --- | --- |
| `Config` | `src/nats_sinks/core/config.py`, `tests/unit/test_config.py` |
| `Logging` | `src/nats_sinks/core/logging.py`, `tests/unit/test_logging.py` |
| `Runner` | `src/nats_sinks/core/runner.py`, `tests/unit/test_commit_then_ack_contract.py` |
| `Envelope` | `src/nats_sinks/core/envelope.py`, `src/nats_sinks/core/consumer.py`, `tests/unit/test_envelope.py` |
| `Encryption` | `src/nats_sinks/core/encryption.py`, `tests/unit/test_encryption.py`, `docs/payload-encryption.md` |
| `Oracle` | `src/nats_sinks/oracle/*`, `tests/unit/test_oracle_*.py`, `docs/oracle-sink.md` |
| `File` | `src/nats_sinks/file/*`, `tests/unit/test_file_*.py`, `docs/file-sink.md` |
| `CI` | `.github/workflows/*`, `scripts/check.sh`, `scripts/security.sh`, `scripts/secret-scan.sh` |
| `Observability` | `src/nats_sinks/observability/*`, `src/nats_sinks/cli/observability.py`, `docs/observability.md`, `docs/prometheus.md`, `docs/otlp.md` |
| `Docs` | `README.md`, `docs/*.md`, `ROBOTS.md`, `AGENTS.md`, `CHANGELOG.md` |
| `N/A` | No current web, session, upload, native-extension, SSRF, or password-authentication surface. |

## Control Register

| ID | Guidance summary | Status | Evidence or disposition |
| --- | --- | --- | --- |
| SD-001 | Treat all external input as hostile until validated, normalized, authorized, and safely handled. | Applied | Config, Envelope, Oracle, File, Docs |
| SD-002 | Apply least privilege for users, services, database accounts, containers, CI jobs, and cloud identities. | Already covered | Oracle least-privilege docs, CI permissions, Docs |
| SD-003 | Fail closed by default for authentication, authorization, validation, configuration, dependency loading, and policy failures. | Applied | Config, Logging, Runner, tests |
| SD-004 | Use defense in depth across validation, authorization, rate limiting, logging, monitoring, isolation, dependency scanning, and runtime controls. | Partially covered | CI, Config, Logging, Observability, Docs; rate limiting remains roadmap |
| SD-005 | Threat-model important features before implementation. | Applied | ROBOTS, AGENTS, Docs |
| SD-006 | Keep security designs simple, explicit, and reviewable. | Already covered | Small modules, typed config, registry, Docs |
| SD-007 | Centralize security-sensitive logic such as auth, validation, encryption, token handling, and audit logging. | Applied | Config, CLI NATS options, Encryption, Logging |
| SD-008 | Make secure behavior the default and risky behavior explicit opt-in. | Already covered | payload logging false, TLS verify true, idempotent modes, File skip_existing |
| SD-009 | Treat internal systems as potentially hostile. | Applied | Docs, Envelope normalization, DLQ safety |
| SD-010 | Document security invariants in code, tests, and architecture notes. | Applied | ROBOTS, AGENTS, commit-then-ACK docs, tests |
| SD-011 | Validate all input at trust boundaries. | Applied | Config, CLI, Envelope, Oracle, File |
| SD-012 | Use allow-list validation for values, formats, types, lengths, ranges, extensions, schemes, and enums. | Already covered | Pydantic literals, SQL identifier regex, file extension validators |
| SD-013 | Reject malformed input early instead of repairing or guessing. | Applied | Config duplicate-key/null-root/size checks |
| SD-014 | Normalize paths, URLs, encodings, Unicode, hostnames, and filenames before validation or comparison. | Already covered | File path resolution, subject component sanitizer, UTF-8 config validation |
| SD-015 | Enforce maximum sizes for bodies, files, JSON, strings, arrays, recursion depth, and batches. | Applied | Delivery batch bounds, config size, mission metadata bounds, and optional core `size_policy` for payload, headers, labels, metadata, record, and batch size |
| SD-016 | Parse structured input with real parsers. | Already covered | JSON parser, Pydantic, no YAML |
| SD-017 | Treat data from databases, caches, logs, and queues as untrusted when it can originate externally. | Already covered | Envelope normalization, Oracle JSON normalization in tests |
| SD-018 | Store validated data in typed internal structures. | Already covered | Pydantic configs, dataclass envelope, typed sink protocols |
| SD-019 | Avoid implicit type coercion for security-sensitive values. | Partially covered | Pydantic strict enums/ranges; future strict-mode review remains roadmap |
| SD-020 | Validate business rules separately from syntax rules. | Already covered | Config model validators, ACK policy literal, Oracle mode rules |
| SD-021 | Use context-specific output encoding for browser, SQL, shell, XML, CSV, logs, URLs, and templates. | Applied | Logging sanitizer, SQL bind variables; web/CSV not present |
| SD-022 | Never concatenate untrusted data into executable contexts. | Already covered | SQL identifiers allow-listed, no shell execution |
| SD-023 | Use parameterized SQL or binding mechanisms. | Already covered | Oracle SQL bind values and tests |
| SD-024 | Use safe template engines with auto-escaping. | Not applicable | No template rendering feature |
| SD-025 | Escape CSV fields that may trigger spreadsheet formulas. | Not applicable | No CSV export feature |
| SD-026 | Restrict dynamic code paths with strict allow lists. | Already covered | Sink registry, literal sink types |
| SD-027 | Treat logs as an injection surface and sanitize control characters. | Applied | Logging, tests |
| SD-028 | Do not build regular expressions from user input unless escaped and bounded. | Already covered | Fixed regexes only |
| SD-029 | Prefer structured APIs over ambiguous text protocols. | Already covered | JSON config, structured NATS/oracledb APIs |
| SD-030 | Keep data and code separated in every interpreter/query/template/shell context. | Already covered | SQL binds, no eval/exec/shell |
| SD-031 | Prefer memory-safe languages and runtimes for new code. | Already covered | Python-only source |
| SD-032 | Replace unsafe C functions in C/C++ code. | Not applicable | No C/C++ source |
| SD-033 | Account for memory corruption risks in native Python extensions and drivers. | Partially covered | Docs and dependency constraints; deeper native sandboxing remains roadmap |
| SD-034 | Keep Python, native wheels, OS libraries, containers, and C/C++ dependencies patched. | Already covered | Dependabot, dependency review, CI |
| SD-035 | Do not pass unchecked sizes or dimensions into native libraries. | Partially covered | Config bounds; broader payload-size limits remain roadmap |
| SD-036 | Validate file headers, lengths, dimensions, ratios, and record counts before parsers/native extensions. | Not applicable | No upload/archive/media parser |
| SD-037 | Use fuzz testing for parsers and native boundaries. | Applied | Deterministic fuzz-style and bounded generator tests cover parsers/normalizers; no native parser boundary |
| SD-038 | Use native compiler hardening for native components. | Not applicable | No native build |
| SD-039 | Use sanitizer tools for native code and Python extensions. | Not applicable | No native build |
| SD-040 | Avoid unsafe pointer arithmetic and manual lifetime management. | Not applicable | No native code |
| SD-041 | Run native parsers for untrusted files in sandboxed worker processes. | Not applicable | No native file parser |
| SD-042 | Treat segfaults and nondeterministic native failures as security bugs. | Roadmap | Add incident guidance if native extensions are introduced |
| SD-043 | Do not expose raw memory views or writable buffers to untrusted plugins. | Applied | Sink connector discovery uses typed descriptors and allow-listed entry points; no raw buffer or plugin scripting API is exposed |
| SD-044 | Prefer maintained libraries for image, archive, crypto, XML, PDF, font, and media parsing. | Already covered | Cryptography library for crypto; no media parsers |
| SD-045 | Add regression tests for crashes, leaks, hangs, bounds errors, or native exceptions. | Roadmap | Regression policy documented; no native incidents |
| SD-046 | Validate integer ranges before arithmetic affecting memory, indexes, permissions, prices, or timeouts. | Already covered | Pydantic bounds on config fields |
| SD-047 | Do not assume Python big integers remove numeric risk. | Partially covered | Config bounds; payload and timing caps roadmap |
| SD-048 | Enforce explicit minimums and maximums for counts, durations, timestamps, and allocation sizes. | Applied | Config bounds and config-size cap |
| SD-049 | Check size arithmetic before allocation or native calls. | Partially covered | Bounded config/batches; broader parser limits roadmap |
| SD-050 | Use decimal-safe types for money and avoid floats for currency/accounting. | Not applicable | No money/accounting feature |
| SD-051 | Reject negative indexes, negative sizes, unexpected zero values, and wraparound-like behavior at boundaries. | Already covered | Pydantic `ge` validators |
| SD-052 | Handle timestamp arithmetic carefully. | Already covered | UTC timestamps and epoch metadata; no auth expiration logic |
| SD-053 | Validate pagination parameters. | Not applicable | No pagination API |
| SD-054 | Bound Python integers before conversion to C integer types. | Partially covered | oracledb values are bound; dedicated C-boundary review roadmap |
| SD-055 | Test boundary values such as 0, 1, -1, max, max+1, empty, and huge values. | Partially covered | Config and fuzz-style tests; property testing roadmap |
| SD-056 | Never pass user-controlled strings as format strings. | Already covered | Logging uses fixed formats |
| SD-057 | Prefer deferred logging interpolation for Python logging. | Already covered | Existing logging call style and formatter |
| SD-058 | Do not expose Python/Jinja/template syntax to untrusted users. | Not applicable | No template feature |
| SD-059 | Treat localization/admin templates as code-like inputs if they interpolate. | Not applicable | No localization/template feature |
| SD-060 | Validate configurable message-template placeholders. | Not applicable | No message-template feature |
| SD-061 | Keep debug formatting, stack traces, and internal dumps out of user-visible responses. | Already covered | CLI catches unexpected errors by type only |
| SD-062 | Avoid shell execution where possible. | Already covered | Production source does not use subprocess/shell |
| SD-063 | Use safe subprocess argument lists with timeouts if subprocesses are required. | Not applicable | No production subprocess use |
| SD-064 | Never concatenate user input into command strings, pipelines, globs, redirections, or env assignments. | Already covered | No production shell execution |
| SD-065 | Use command allow lists for command names, flags, paths, and modes. | Not applicable | No production command execution |
| SD-066 | Give subprocesses a minimal environment without inherited secrets. | Not applicable | No production subprocesses |
| SD-067 | Give subprocesses timeouts, working directories, resource limits, and output limits. | Not applicable | No production subprocesses |
| SD-068 | Treat filenames beginning with `-` as dangerous for CLI tools. | Not applicable | No production shelling out with filenames |
| SD-069 | Prefer library APIs over shelling out to tools. | Already covered | nats-py, oracledb, Python gzip, Python build tooling |
| SD-070 | Sanitize subprocess output before logging or returning it. | Not applicable | No production subprocesses |
| SD-071 | Run risky external tools in isolated low-privilege contexts. | Not applicable | No production external tools |
| SD-072 | Use bound SQL parameters for SQL values. | Already covered | Oracle |
| SD-073 | Allow-list dynamic table names, columns, operators, and directions. | Already covered | Oracle identifier validation |
| SD-074 | Do not expose raw query languages without a strict policy layer. | Already covered | Config does not accept raw SQL |
| SD-075 | Apply authorization after query construction and before returning results. | Not applicable | No query API exposed to users |
| SD-076 | Limit query cost with pagination, timeouts, result caps, and complexity controls. | Partially covered | Batch writes only; Oracle timeouts/cost controls roadmap |
| SD-077 | Do not reveal DB schema names, constraints, indexes, or query fragments in user errors. | Partially covered | Framework errors are concise; deeper driver-error redaction roadmap |
| SD-078 | Use separate database accounts for read-only, migrations, workers, analytics, and admin. | Already covered | Oracle least-privilege docs |
| SD-079 | Treat ORM/raw SQL escape hatches as high risk. | Already covered | No ORM; small SQL builder under tests |
| SD-080 | Log query metadata safely without sensitive parameters or payloads. | Already covered | No bind-value logging |
| SD-081 | Add injection tests for search/filter/sort/report/export parameters. | Not applicable | No search/filter/report/export API |
| SD-082 | Escape untrusted content for exact browser context. | Not applicable | No browser UI |
| SD-083 | Keep template auto-escaping enabled and avoid safe/raw bypasses. | Not applicable | No template rendering |
| SD-084 | Use strong CSP as defense in depth. | Not applicable | No browser app |
| SD-085 | Sanitize user-provided HTML with a proven sanitizer. | Not applicable | No HTML input |
| SD-086 | Never place untrusted data directly in inline JS, handlers, style attrs, or dangerous URLs. | Not applicable | No browser app |
| SD-087 | Use HttpOnly, Secure, and SameSite for sensitive cookies. | Not applicable | No cookies |
| SD-088 | Do not store privileged tokens in browser-accessible storage. | Not applicable | No browser storage |
| SD-089 | Validate redirect targets with allow lists. | Not applicable | No redirect feature |
| SD-090 | Treat Markdown, rich text, SVG, MathML, uploaded HTML, and embedded docs as active content. | Not applicable | No user-content rendering/upload |
| SD-091 | Test reflected, stored, and DOM XSS paths. | Not applicable | No browser app |
| SD-092 | Protect state-changing browser requests with CSRF controls. | Not applicable | No browser app |
| SD-093 | Use cryptographically strong session IDs, reset tokens, invite tokens, CSRF tokens, and API keys. | Not applicable | No session/token issuing feature |
| SD-094 | Use Python `secrets` for security-sensitive tokens. | Already covered | Encryption tests use `secrets`; crypto uses library randomness |
| SD-095 | Store only opaque session IDs in cookies and keep state server-side. | Not applicable | No sessions |
| SD-096 | Rotate session IDs after login and privilege changes. | Not applicable | No sessions |
| SD-097 | Expire sessions and tokens based on risk, inactivity, lifetime, and revocation. | Not applicable | No token issuing feature |
| SD-098 | Bind high-risk actions to recent auth or MFA. | Not applicable | No user auth workflow |
| SD-099 | Never place bearer tokens in URLs. | Already covered | Docs prefer env secrets; no token URLs |
| SD-100 | Use constant-time comparison for secrets, MACs, tokens, and signatures. | Roadmap | Needed if future auth/signature verification is added |
| SD-101 | Revoke sessions and tokens after security events. | Not applicable | No sessions/tokens managed |
| SD-102 | Use mature auth libraries or identity providers. | Not applicable | No custom auth server |
| SD-103 | Store passwords only with Argon2id, bcrypt, or scrypt. | Not applicable | No password storage |
| SD-104 | Never hash passwords with fast hashes alone. | Not applicable | No password storage |
| SD-105 | Enforce authorization on every object-level action. | Not applicable | No multi-user object API |
| SD-106 | Prevent IDOR with exact resource authorization. | Not applicable | No resource API |
| SD-107 | Deny access by default when identity, tenant, role, ownership, policy, or state is missing. | Not applicable | No access-control engine |
| SD-108 | Separate authentication from authorization. | Not applicable | No auth subsystem |
| SD-109 | Use RBAC, ABAC, or policy-based access consistently. | Roadmap | Relevant to future management UI/API |
| SD-110 | Log privilege changes, failed authz, admin actions, and sensitive account events. | Not applicable | No account/admin subsystem |
| SD-111 | Test authorization across tenants, roles, owners, disabled users, and partial accounts. | Not applicable | No auth subsystem |
| SD-112 | Use established crypto libraries and protocols. | Already covered | Encryption uses `cryptography` AES modes |
| SD-113 | Use authenticated encryption modes for application data. | Already covered | AES-256-GCM and AES-256-CCM |
| SD-114 | Never reuse nonces/IVs/salts/one-time keys where uniqueness is required. | Already covered | Crypto library random nonce generation |
| SD-115 | Keep cryptographic keys separate from encrypted data. | Already covered | `key_b64_env`, docs; direct keys test-only |
| SD-116 | Rotate keys with versioning. | Applied | Payload envelopes include `key_id`; `PayloadKeyRegistry` supports multi-key decryption during rotation windows; provider-managed automatic rotation remains future optional connector work |
| SD-117 | Use TLS for network communication. | Already covered | NATS TLS options and docs |
| SD-118 | Do not disable certificate or hostname validation for convenience. | Already covered | TLS verify true by default, docs warn |
| SD-119 | Use modern hashes for integrity/signatures and password hashes for passwords. | Already covered | SHA-256 for diagnostics only; no password hashes |
| SD-120 | Use HMAC or digital signatures for authenticity instead of plain hashes. | Applied | Optional message authenticity verification supports HMAC-SHA256 and Ed25519 before sink delivery |
| SD-121 | Do not treat Base64, hex, JWT body data, or URL encoding as encryption. | Already covered | Docs explicitly distinguish key encoding/encryption |
| SD-122 | Keep secrets out of source, history, logs, tickets, fixtures, images, and bundles. | Applied | Secret scan, docs |
| SD-123 | Load secrets from managed stores or secure runtime config. | Already covered | `password_env`, `token_env`, `key_b64_env` |
| SD-124 | Scope secrets narrowly. | Already covered | Least-privilege docs and examples |
| SD-125 | Rotate credentials regularly and after exposure. | Already covered | Security docs |
| SD-126 | Do not pass secrets through command-line arguments. | Already covered | Env references documented |
| SD-127 | Redact secrets in logs, exceptions, metrics, traces, debug pages, and crash reports. | Already covered | Redacted config, safe CLI errors |
| SD-128 | Use separate credentials for dev, test, staging, production, CI, and tooling. | Already covered | Docs |
| SD-129 | Prefer short-lived auditable credentials over long-lived personal tokens. | Already covered | Release docs and gh auth preflight |
| SD-130 | Add secret scanning to pre-commit and CI. | Applied | `scripts/secret-scan.sh`, CI, pre-commit |
| SD-131 | Treat environment variables as sensitive but not magically secure. | Already covered | Docs |
| SD-132 | Never deserialize untrusted data with pickle/marshal/shelve/unsafe YAML/object formats. | Already covered | JSON-only config; no pickle |
| SD-133 | Use data-only formats for untrusted input. | Already covered | JSON |
| SD-134 | Validate deserialized data against a schema. | Already covered | Pydantic |
| SD-135 | Ensure deserialization cannot instantiate classes or execute hooks. | Already covered | `json.loads` with schema validation |
| SD-136 | Treat signed serialized objects as risky when keys/classes are widely accessible. | Not applicable | No signed object serialization |
| SD-137 | Version serialized formats explicitly. | Already covered | Payload/encryption envelopes include schema/version |
| SD-138 | Keep deserialization code small, isolated, fuzz-tested, and reviewed. | Applied | Small config/payload parsers plus deterministic generator tests |
| SD-139 | Reject unknown fields when strictness improves safety. | Already covered | Pydantic `extra="forbid"` |
| SD-140 | Do not deserialize user data into privileged objects with methods/side effects. | Already covered | Data-only dicts and Pydantic config |
| SD-141 | Log deserialization failures safely without echoing large or malicious payloads. | Already covered | Config errors avoid payload dumps |
| SD-142 | Store uploaded files outside executable paths and serve via controlled handlers. | Not applicable | No uploads |
| SD-143 | Generate server-side filenames instead of trusting user filenames. | Already covered | File sink deterministic sanitized filenames |
| SD-144 | Validate file type with content inspection where possible. | Not applicable | No uploaded file type acceptance |
| SD-145 | Enforce file size, count, dimensions, page-count, decompressed size, and processing time limits. | Partially covered | Config and batch bounds; file output quotas roadmap |
| SD-146 | Scan or sandbox high-risk uploads. | Not applicable | No uploads |
| SD-147 | Prevent path traversal by resolving paths to a known base directory. | Already covered | File sink `_safe_destination` |
| SD-148 | Treat symlinks, hard links, mount points, case-insensitive paths, Unicode lookalikes, and ADS as attack surfaces. | Partially covered | Base resolution exists; advanced filesystem policy roadmap |
| SD-149 | Avoid TOCTOU bugs between checks and file use. | Already covered | Atomic temp file placement and link/replace behavior |
| SD-150 | Extract archives safely with path and size checks. | Not applicable | No archive extraction |
| SD-151 | Prevent zip/decompression bombs. | Partially covered | No archive extraction; gzip output only |
| SD-152 | Disable external entity resolution and network access for XML parsing. | Not applicable | No XML parsing |
| SD-153 | Protect XML parsers against XXE, entity expansion, DTD abuse, and nesting. | Not applicable | No XML parsing |
| SD-154 | Use safe YAML loaders only. | Already covered | YAML removed; JSON only |
| SD-155 | Limit JSON body size, nesting depth, object count, array length, and string length. | Applied | Config size, duplicate-key checks, mission metadata depth/count/string bounds, and optional core `size_policy` for sink-bound records |
| SD-156 | Reject duplicate or ambiguous JSON keys where security-sensitive. | Applied | Config duplicate-key rejection |
| SD-157 | Avoid parser differentials across frontend/backend/proxy/downstream services. | Already covered | Single JSON parser path for config |
| SD-158 | Treat content type as advisory and verify payload format. | Not applicable | No HTTP content-type boundary |
| SD-159 | Use schema validation for APIs and configuration files. | Already covered | Pydantic |
| SD-160 | Keep parsers updated. | Already covered | Dependabot/dependency review |
| SD-161 | Fuzz custom parsers/converters with malformed and oversized inputs. | Applied | Deterministic fuzz-style and bounded generator tests cover malformed, oversized, control-character, and traversal-like cases |
| SD-162 | Treat user-supplied URLs as dangerous. | Not applicable | No server-side fetch from user URLs |
| SD-163 | Allow-list URL schemes, hostnames, ports, and services for server-side fetches. | Not applicable | No server-side fetch |
| SD-164 | Block private/loopback/link-local/multicast/metadata IPs after DNS and redirects. | Not applicable | No server-side fetch |
| SD-165 | Re-resolve and revalidate destinations on redirects. | Not applicable | No server-side fetch |
| SD-166 | Disable redirects unless required and safely validated. | Not applicable | No server-side fetch |
| SD-167 | Set strict connection/read/total/response-size limits for HTTP clients. | Roadmap | Relevant to future HTTP sink |
| SD-168 | Do not send internal credentials to user-controlled URLs. | Roadmap | Relevant to future HTTP sink |
| SD-169 | Restrict network egress from containers. | Roadmap | Docker/Kubernetes hardening |
| SD-170 | Log outbound destination metadata for high-risk fetches. | Roadmap | Relevant to future HTTP sink |
| SD-171 | Use a hardened proxy for SSRF policy where practical. | Roadmap | Relevant to future HTTP sink |
| SD-172 | Avoid check-then-act security logic when resources can change. | Already covered | File atomic writes, DB constraints |
| SD-173 | Use atomic DB constraints, transactions, CAS, locks, or unique indexes. | Already covered | Oracle commit and idempotency constraints |
| SD-174 | Treat races affecting auth, balances, quotas, inventory, idempotency, or privileges as security bugs. | Already covered | Idempotency tests and docs |
| SD-175 | Use idempotency keys for retried operations. | Already covered | Envelope keys, Oracle modes, File deterministic names |
| SD-176 | Keep critical sections small and avoid network calls while locks are held. | Already covered | No explicit locks |
| SD-177 | Use lock timeouts. | Not applicable | No explicit locks |
| SD-178 | Prefer immutable data and message passing over shared mutable state. | Already covered | Immutable envelope |
| SD-179 | Do not rely on the GIL for application-level thread safety. | Already covered | Async design and `to_thread` isolation |
| SD-180 | Use database-level guarantees across processes/hosts. | Already covered | Oracle constraints/merge modes |
| SD-181 | Add stress/repeated concurrent tests for race-prone flows. | Roadmap | Load/concurrency testing before `1.0.0` |
| SD-182 | Set limits on size, memory, CPU, recursion, regex complexity, queue depth, and concurrent work. | Partially covered | Batch/config limits; broader runtime quotas roadmap |
| SD-183 | Use rate limiting, quotas, backpressure, and admission control for expensive operations. | Partially covered | Pull batch backpressure; quotas/rate limiting roadmap |
| SD-184 | Avoid unbounded caches, queues, recursion, process/task fan-out. | Already covered | Bounded batches and max in-flight |
| SD-185 | Give every network/database/cache/lock/subprocess/external call a timeout. | Partially covered | Fetch timeouts; Oracle/NATS timeout review roadmap |
| SD-186 | Bound retries with exponential backoff and jitter. | Applied | Delivery retry policy supports fixed, linear, exponential, cap, and jitter controls |
| SD-187 | Detect and reject algorithmic complexity attacks. | Partially covered | Input bounds; regex simple/fixed |
| SD-188 | Stream large files/responses instead of loading entirely. | Partially covered | Config bounded; file sink writes one record at a time |
| SD-189 | Put heavy work behind queues with limits/timeouts/cancellation/quotas. | Roadmap | Advanced worker model not present |
| SD-190 | Degrade gracefully under load. | Partially covered | Backpressure config; load-shedding roadmap |
| SD-191 | Monitor saturation signals. | Partially covered | Basic runner counters and gauges exist; external exporter and saturation alerting remain roadmap |
| SD-192 | Pin direct dependencies and use lock files for reproducible builds. | Partially covered | Version ranges; lock/hash installs roadmap |
| SD-193 | Use hash-verified installs in high-trust environments. | Roadmap | Documented future packaging hardening |
| SD-194 | Scan dependencies, images, OS packages, Actions, Dockerfiles, and CI. | Already covered | Dependabot, dependency review, CodeQL, CI |
| SD-195 | Prefer maintained packages with security response processes. | Already covered | Minimal dependency set |
| SD-196 | Consider typosquatting, dependency confusion, abandoned packages, and malicious installers. | Already covered | Docs and minimal dependency policy |
| SD-197 | Separate public/private package indexes to avoid dependency confusion. | Roadmap | Packaging deployment guidance improvement |
| SD-198 | Do not install dependencies dynamically at runtime. | Already covered | No runtime installers |
| SD-199 | Review dependency updates for changed security assumptions. | Already covered | Dependabot/dependency review |
| SD-200 | Remove unused dependencies. | Already covered | Minimal dependency surface |
| SD-201 | Generate and store SBOMs for important artifacts where justified. | Applied | `scripts/sbom.sh`, CI, release workflow, and `docs/sbom.md` generate and document CycloneDX SBOM evidence |
| SD-202 | Avoid eval, exec, dynamic imports, dynamic attrs, and runtime code generation for untrusted input. | Already covered | Safe registry; Oracle driver import not config-controlled |
| SD-203 | Replace dynamic execution with dispatch tables, enums, schemas, or safe evaluators. | Already covered | SinkRegistry, Literals, Pydantic |
| SD-204 | Avoid pickle and unsafe YAML for untrusted data. | Already covered | JSON-only |
| SD-205 | Use `secrets` instead of `random` for security randomness. | Already covered | Tests and crypto |
| SD-206 | Use constant-time comparison for secrets. | Roadmap | Needed if future auth/signature checks are added |
| SD-207 | Use pathlib and resolved base-directory checks for filesystem paths. | Already covered | File |
| SD-208 | Use secure temporary files. | Already covered | `tempfile.mkstemp` |
| SD-209 | Avoid `shell=True`. | Already covered | No production subprocess |
| SD-210 | Use isolated virtual environments; do not rely on global packages. | Already covered | Docs/CI install extras |
| SD-211 | Use isolated or safe-path Python execution for hardened CLI tools. | Roadmap | Future packaging hardening |
| SD-212 | Treat ctypes, cffi, C extensions, and native wheels as hardened boundaries. | Partially covered | Docs; no direct FFI |
| SD-213 | Avoid mutable default arguments. | Already covered | dataclasses default factories and Pydantic defaults |
| SD-214 | Avoid broad monkey-patching in production code. | Already covered | Tests only monkeypatch |
| SD-215 | Do not expose debug servers, reloaders, consoles, notebooks, or tracebacks. | Already covered | No server/debug UI |
| SD-216 | Use type hints and static analysis. | Already covered | mypy, py.typed |
| SD-217 | Catch only safely handled exceptions. | Already covered | Boundary catches route to framework errors |
| SD-218 | Do not use empty except blocks or broad exception swallowing. | Partially covered | Some cleanup suppressions are scoped; review continues |
| SD-219 | Return generic user errors while logging internal detail for operators. | Already covered | CLI unexpected errors expose type only |
| SD-220 | Do not expose stack traces, SQL, paths, env vars, dependency versions, secrets, or hostnames to users. | Partially covered | CLI guards; deeper driver-error redaction roadmap |
| SD-221 | Structured logs should include IDs, operation, status, latency, and error categories. | Partially covered | Basic logging; structured observability roadmap |
| SD-222 | Redact passwords, tokens, cookies, auth headers, keys, payment data, and PII from logs. | Already covered | Redaction and payload logging default false |
| SD-223 | Log security-relevant events. | Partially covered | Lifecycle/failure logs; audit event taxonomy roadmap |
| SD-224 | Sanitize logs against injection. | Applied | Logging |
| SD-225 | Make audit logs tamper-resistant and access-controlled. | Roadmap | Deployment/operator responsibility; docs expansion needed |
| SD-226 | Monitor logs and alerts for anomalies. | Roadmap | Observability/exporter roadmap |
| SD-227 | Keep functions small, deterministic, and side-effect-light. | Already covered | Module layout and tests |
| SD-228 | Validate external input at boundaries, then pass typed internal objects. | Already covered | Config, Envelope |
| SD-229 | Keep configuration explicit, validated, and safe by default. | Applied | Config |
| SD-230 | Startup checks should verify config, credentials, migrations, dependency reachability, and versions. | Partially covered | CLI validate/test-sink; migration/version checks roadmap |
| SD-231 | Graceful shutdown stops work, handles in-flight tasks, flushes telemetry, and closes connections. | Already covered | Runner lifecycle and sink stop |
| SD-232 | Design operations to be idempotent. | Already covered | Commit-then-ACK and sink idempotency |
| SD-233 | Separate retryable from permanent errors. | Already covered | Error hierarchy |
| SD-234 | Use circuit breakers, bulkheads, and fallback behavior for fragile dependencies. | Roadmap | Advanced resilience controls |
| SD-235 | Avoid hidden global state. | Already covered | Instance-local runtime |
| SD-236 | Prefer dependency injection over hardcoded clients/env/module init. | Already covered | Sink injection, config resolution |
| SD-237 | Health checks should reflect readiness. | Already covered | File/Oracle health checks |
| SD-238 | Use feature flags, canaries, rollouts, kill switches, and rollback for risky changes. | Roadmap | Deployment docs |
| SD-239 | Keep migrations backward-compatible during rolling deployments. | Partially covered | Docs warn Oracle migration; migration tooling roadmap |
| SD-240 | Design for clock skew, duplicates, partial failures, delayed jobs, and out-of-order events. | Already covered | At-least-once/idempotency docs |
| SD-241 | Treat flaky tests as production risk. | Already covered | Testing docs |
| SD-242 | Measure performance before optimization. | Already covered | Performance docs |
| SD-243 | Use profiling tools to find bottlenecks. | Roadmap | Benchmark/profiling scripts |
| SD-244 | Optimize algorithms/data access before micro-optimizing syntax. | Already covered | Performance docs |
| SD-245 | Avoid O(n²) request paths unless limits make them safe. | Already covered | Bounded batches and simple loops |
| SD-246 | Use appropriate data structures. | Already covered | dict/set/tuple usage |
| SD-247 | Stream large data instead of loading full files/responses/result sets. | Partially covered | Bounded config; future large payload guidance |
| SD-248 | Batch external calls to reduce round trips. | Already covered | Batch sink writes |
| SD-249 | Use connection pooling. | Already covered | Oracle pool, NATS connection reuse |
| SD-250 | Use caching only with invalidation, memory limits, TTLs, and correctness rules. | Not applicable | No runtime cache |
| SD-251 | Set cache maximum sizes. | Not applicable | No runtime cache |
| SD-252 | Avoid excessive object allocation in hot paths. | Partially covered | Readable implementation; profiling roadmap |
| SD-253 | Precompile repeated regexes. | Already covered | SQL/file regex constants |
| SD-254 | Use join/builders for large string construction. | Already covered | SQL builders and JSON serialization |
| SD-255 | Prefer built-ins, comprehensions, stdlib, and vectorized libraries when clearer/faster. | Already covered | Standard-library heavy implementation |
| SD-256 | Choose threads/processes/async according to workload. | Already covered | Async NATS, thread-isolated file/Oracle blocking calls |
| SD-257 | Monitor p95/p99 latency. | Partially covered | `sink_batch_write_seconds` observations exist; histogram exporter and p95/p99 alerting remain roadmap |
| SD-258 | Treat memory/file-descriptor/thread/queue growth as stability bugs. | Already covered | Docs and bounded config |
| SD-259 | Load tests should include realistic size, concurrency, latency, dependency slowness, and failure modes. | Roadmap | Load-test plan |
| SD-260 | Keep hot code readable unless profiling proves optimization necessary. | Already covered | Code style |
| SD-261 | Maintain layered tests: unit, integration, contract, regression, property, fuzz, load, failure injection. | Partially covered | Unit/integration/contract/regression/property-style/fuzz-style covered; load and broader failure-injection roadmap |
| SD-262 | Test negative paths. | Already covered | Unhappy-path tests |
| SD-263 | Add regression tests for every security bug, crash, race, corruption, or outage. | Already covered | Policy in docs; no such incidents recorded |
| SD-264 | Use property-based tests for parsers, validators, serializers, auth, transforms. | Applied | Deterministic bounded generator tests cover subjects, payloads, metadata, mission metadata, and file paths |
| SD-265 | Use fuzzing for file parsers, protocol handlers, native boundaries, regex-heavy logic, complex input. | Partially covered | Deterministic fuzz/property-style tests exist; full fuzz tooling remains roadmap if native or complex protocol parsers are introduced |
| SD-266 | Run static analysis, linting, typing, dependency scanning, secret scanning, formatting in CI. | Applied | CI |
| SD-267 | Security tests should cover SQLi, command injection, traversal, SSRF, XSS, CSRF, deserialization, IDOR, rate-limit bypass. | Partially covered | SQL/traversal/deserialization covered; web/SSRF not applicable yet |
| SD-268 | Test authorization with roles, tenants, ownership, disabled users, stale sessions, and privilege changes. | Not applicable | No authorization subsystem |
| SD-269 | Run concurrency tests repeatedly for race-prone flows. | Roadmap | Stress/concurrency test plan |
| SD-270 | Test performance limits and failure behavior before production traffic. | Roadmap | Benchmark and load tests |
| SD-271 | Run services as non-root with minimal privileges. | Roadmap | Docker/Kubernetes service examples |
| SD-272 | Use read-only filesystems where practical and explicit write dirs. | Roadmap | Deployment hardening docs |
| SD-273 | Restrict outbound network access. | Roadmap | Deployment hardening docs |
| SD-274 | Isolate production, staging, testing, and development. | Already covered | Docs |
| SD-275 | Patch OS, runtimes, base images, libraries, and infrastructure. | Already covered | Dependabot/dependency review; image roadmap |
| SD-276 | Use immutable build artifacts. | Already covered | PyPI build/release workflow |
| SD-277 | Avoid unreproducible manual production changes. | Already covered | Release docs |
| SD-278 | Encrypt, protect, test, and restore backups. | Not applicable | Backup operations are destination/operator responsibility |
| SD-279 | Monitor SLIs such as latency, errors, traffic, saturation, queue lag, dependency health. | Partially covered | Basic traffic/error/latency gauges and counters exist; queue lag/dependency-health exporter roadmap |
| SD-280 | Alert on user-impacting symptoms, not only machine metrics. | Roadmap | Operations guidance |
| SD-281 | Practice incident response with ownership, rollback, comms, postmortems. | Roadmap | Governance/operations expansion |
| SD-282 | Remove debug endpoints, sample apps, default creds, unused services, open ports. | Already covered | No server endpoints/default creds |
| SD-283 | Make production access auditable, time-bound, least-privileged, and MFA-protected. | Roadmap | Operator/platform responsibility docs |
| SD-284 | Validate, normalize, bound, and reject external input by default. | Applied | Config, Envelope, File, Oracle |
| SD-285 | Enforce authorization for exact subject, action, resource, tenant, and object state. | Not applicable | No authz subsystem |
| SD-286 | Keep code/data separated in SQL, shell, HTML, JS, XML, LDAP, regex, templates. | Already covered | SQL binds, no shell/web |
| SD-287 | Keep secrets out of code, logs, traces, tests, images, tickets, client assets. | Applied | Secret scan and redaction |
| SD-288 | Prevent unsafe deserialization from instantiating objects or executing code. | Already covered | JSON-only |
| SD-289 | Keep file paths/uploads/archives/temp files inside intended storage. | Already covered | File sink path resolution |
| SD-290 | Use fixed executable, argument lists, timeouts, minimal env, no shell for subprocess. | Not applicable | No production subprocess |
| SD-291 | External calls require timeouts, bounded retries, backoff, jitter, idempotency. | Partially covered | Delivery retry jitter/backoff and idempotency covered; broader timeout review remains roadmap |
| SD-292 | Bound caches, queues, payloads, and batch operations. | Partially covered | Batch/config bounds; payload runtime caps roadmap |
| SD-293 | Handle errors explicitly without hiding bugs or leaking internals. | Already covered | Error hierarchy and CLI boundaries |
| SD-294 | Use security-aware logging without sensitive data. | Applied | Logging sanitizer and redaction |
| SD-295 | Pin, scan, justify, and maintain dependencies. | Partially covered | Constrained deps/scanning/SBOM; lock and hash-verified install guidance remains roadmap |
| SD-296 | Validate sizes/lifetimes/input at native boundaries. | Partially covered | No direct native calls; driver boundary review roadmap |
| SD-297 | Measure performance before optimization. | Already covered | Performance docs |
| SD-298 | Test normal, failure, abuse, edge, and concurrency risks. | Partially covered | Broad tests; concurrency/load roadmap |
| SD-299 | Use parameterized SQL exclusively. | Already covered | Oracle |
| SD-300 | Avoid eval, exec, unsafe dynamic imports, and user-controlled code execution. | Already covered | No eval/exec; safe registry |
| SD-301 | Avoid pickle and unsafe deserialization for untrusted data. | Already covered | JSON-only |
| SD-302 | Use `secrets` or secure OS randomness for tokens and keys. | Already covered | Encryption/tests |
| SD-303 | Use constant-time comparison for secrets. | Roadmap | Future auth/signature features |
| SD-304 | Use safe subprocess argument lists with `shell=False`. | Not applicable | No production subprocess |
| SD-305 | Apply strict input validation and output encoding at trust boundaries. | Applied | Config, Logging, SQL, File |
| SD-306 | Use strong password hashing and mature authentication libraries. | Not applicable | No password auth implementation |
| SD-307 | Apply object-level authorization checks to sensitive operations. | Not applicable | No multi-user object API |
| SD-308 | Use pinned dependencies and reproducible builds. | Partially covered | Constrained deps and reproducible SBOM output; lock/hash guidance remains roadmap |
| SD-309 | Give every external operation a timeout. | Partially covered | NATS fetch timeout; fuller external timeout audit roadmap |
| SD-310 | Use bounded retries with backoff, jitter, and idempotency. | Applied | Delivery retry policy plus commit-then-ACK and sink idempotency |
| SD-311 | Use structured, redacted, security-aware logging. | Applied | Logging and redaction |
| SD-312 | Use type hints, linting, static analysis, and boundary validation. | Already covered | mypy/Ruff/Pydantic |
| SD-313 | Use fuzzing and property tests for complex input handling. | Applied | Deterministic fuzz-style tests and bounded generator tests cover current complex input handling |
| SD-314 | Use profiling, tracing, and metrics before performance optimization. | Partially covered | Basic sink-write timing exists; profiling and tracing remain roadmap |
| SD-315 | Use resource limits, sandboxing, graceful shutdown, health checks, monitoring, and safe rollouts. | Partially covered | Graceful shutdown/health checks; sandboxing/rollouts roadmap |
| SD-316 | Treat crashes, hangs, memory growth, corruption, and flaky behavior as reliability and security signals. | Already covered | Docs, tests, ROBOTS/AGENTS |

## Additional Project-Specific Controls

The 316 controls above are broad secure-development guidance. During review,
the project also surfaced a few `nats-sinks`-specific practices that are worth
tracking as controls because they protect release integrity, public API
stability, and documentation quality.

| ID | Project-specific practice | Status | Evidence or disposition |
| --- | --- | --- | --- |
| NS-001 | Documented imports are part of the public compatibility contract and must be tested before release. | Applied | `tests/unit/test_public_api.py` verifies package exports, sink extension points, configuration helpers, production sink imports, and console-script entry points; `docs/public-api.md` documents the contract. |
| NS-002 | Package version metadata must stay consistent across `pyproject.toml`, `nats_sinks.__version__`, the README, the documentation home page, and the changelog. | Applied | `scripts/check-version-consistency.py`, `scripts/check.sh`, CI, and pre-commit enforce release-version consistency. |
| NS-003 | README links that render on PyPI must remain fully qualified public URLs, while MkDocs page-to-page links should remain version-local. | Already covered | `scripts/check-markdown-links.py` and release documentation. |
| NS-004 | Generated documentation output under `site/` is build output, not the Markdown source of truth. | Applied | `ROBOTS.md` and `AGENTS.md` instruct agents to edit `docs/`, README, and config files rather than generated HTML. |
| NS-005 | Every production sink must remain part of the sink capability checks before release. | Already covered | `scripts/check-sinks.sh` runs file sink, encrypted file sink, Oracle mapping, and CLI smoke coverage. |
| NS-006 | Security-review findings that change the project posture must update this register, the agent guidance, tests, documentation, and `CHANGELOG.md` together. | Applied | `ROBOTS.md`, `AGENTS.md`, `CHANGELOG.md`, and this page. |
| NS-007 | The latest test report must remain sanitized and should summarize the newest validation run without exposing live infrastructure details. | Already covered | `docs/test-report.md` retention and redaction policy. |
| NS-008 | Release automation must use short-lived credentials or trusted publishing and must not depend on committed tokens. | Already covered | Release workflow permissions, release docs, and GitHub CLI authentication preflight. |
| NS-009 | Any future HTTP, web UI, upload, archive, native extension, or plugin-execution feature must reopen the relevant `Not applicable` controls before implementation. | Applied | Follow-up items below and `ROBOTS.md`/`AGENTS.md` threat-modeling guidance. |
| NS-010 | Runtime version drift is a release-blocking issue because operators, support teams, and package consumers rely on coherent version reporting. | Applied | `src/nats_sinks/__init__.py` is now aligned with `pyproject.toml`, and automated checks guard future drift. |

## Follow-Up Items

The current hardening pass produced code and documentation changes where the
controls matched the present package surface. The most important follow-up
items are:

1. Evaluate whether Hypothesis or another dedicated property-testing tool is
   needed after the deterministic bounded generator suite reaches its limits.
2. Add a repeatable load-test profile covering NATS fetch, sink mapping,
   backend write, commit, ACK, retries, and DLQ behavior.
3. Add hash-verified install guidance for high-trust environments.
4. Add more explicit container/Kubernetes hardening guidance once official
   container images are introduced.
5. Reopen the web/session/SSRF/upload/native-code controls before adding an
   HTTP sink with outbound user-configured destinations, a web UI, upload
   handling, archive parsing, or native extensions.
