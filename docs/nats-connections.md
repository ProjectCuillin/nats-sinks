# NATS Connections And Authentication

This page documents how `nats-sinks` connects to a NATS server and how current
authentication settings are represented in JSON configuration.

The implementation uses `nats-py` connection options under the hood. The CLI
loads JSON, redacts secrets for display, resolves secret environment variables
only when opening a connection, then passes the resulting options to
`nats.connect`.

In controlled mission and defence networks, connection policy is often as
important as application code. Keep NATS authentication, TLS trust, account
layout, and subject permissions aligned with the classification and operational
domains carried by the stream.

## Supported In This Release

The current release supports these production-use connection patterns:

- unauthenticated local development connections,
- token authentication with `token` or `token_env`,
- plain username/password authentication with `user` and `password` or
  `password_env`,
- server-side bcrypted username/password credentials, using the same client
  configuration as plain username/password,
- TLS server verification with a local CA file, including private or
  self-signed NATS server CAs,
- optional TLS client certificate/key transport settings passed through to
  `nats-py`.
- optional multiple NATS seed URLs for clustered deployments,
- reconnect tuning for connection timeout, reconnect wait, maximum reconnect
  attempts, ping behavior, pending buffer size, and drain timeout,
- connection event metrics for disconnect, reconnect, close, discovered-server,
  and asynchronous error callbacks, and
- least-privilege NATS permission templates for runtime workers, DLQ publish
  rights, optional consumer management, and advisory readers.

Advanced identity models such as TLS certificate authentication policy, NKEY
challenge authentication, and decentralized JWT authentication/authorization are
tracked on the roadmap for deeper certification and documentation.

## Connection Flow

```mermaid
sequenceDiagram
    participant CLI as nats-sink CLI
    participant Cfg as JSON config
    participant Env as Environment
    participant TLS as SSLContext
    participant NP as nats-py
    participant NS as NATS server

    CLI->>Cfg: load config.json
    CLI->>CLI: validate with Pydantic
    CLI->>Env: resolve token_env or password_env when needed
    CLI->>TLS: build SSLContext when tls:// or TLS files are configured
    CLI->>NP: nats.connect(servers, options)
    NP->>NS: authenticate and establish connection
    NP-->>CLI: connection event callbacks
    CLI->>CLI: increment connection metrics
```

The resolved token or password is never included in redacted config output. If
the required environment variable is missing, startup fails before the runner
begins consuming messages.

## Local Development Without Authentication

For local-only development:

```json
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*"
  }
}
```

This is appropriate for a developer machine or isolated test environment. It is
not recommended for production.

## Token Authentication

Token authentication uses a single shared secret. Prefer `token_env` so the
secret is injected by the service manager, container platform, or secret store.

```json
{
  "nats": {
    "url": "tls://nats.example.com:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*",
    "token_env": "NATS_TOKEN",
    "tls_ca_file": "/etc/nats/certs/ca.crt"
  }
}
```

Then configure the environment:

```bash
export NATS_TOKEN='example-client-token'
```

Direct `token` values are supported for tests and disposable local examples, but
should not be committed to repository files.

Token authentication is mutually exclusive with the other client
authentication methods. A configuration that combines `token` or `token_env`
with username/password, `creds_file`, or `nkey_seed_file` fails validation
before any connection attempt is made.

## Plain Username/Password Authentication

Plain username/password authentication uses `user` plus either `password_env` or
`password`.

```json
{
  "nats": {
    "url": "tls://nats.example.com:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*",
    "user": "orders_sink",
    "password_env": "NATS_PASSWORD",
    "tls_ca_file": "/etc/nats/certs/ca.crt"
  }
}
```

Then configure the environment:

```bash
export NATS_PASSWORD='example-client-password'
```

Use TLS for username/password authentication in production. Without TLS, the
client credential can be exposed to the network path.

The username and password source must be configured together. A password source
without `user`, or a `user` without `password` or `password_env`, is rejected as
an incomplete authentication mode.

## Bcrypted Username/Password Credentials

NATS can store bcrypted passwords in the server configuration. This protects the
server-side configuration file from storing clear-text passwords.

The client configuration is unchanged: `nats-sinks` still sends the clear-text
client password to the server, and the server verifies it against the bcrypt
hash.

```json
{
  "nats": {
    "url": "tls://nats.example.com:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*",
    "user": "orders_sink",
    "password_env": "NATS_PASSWORD",
    "tls_ca_file": "/etc/nats/certs/ca.crt"
  }
}
```

A server-side configuration might contain a bcrypt hash similar to:

```text
authorization {
  users = [
    {
      user: "orders_sink"
      password: "$2a$11$..."
    }
  ]
}
```

Do not put the bcrypt hash in the `nats-sinks` client config. The hash belongs
on the NATS server. The client receives the clear-text password through
`NATS_PASSWORD`, and TLS protects that secret in transit.

Do not combine `creds_file` with token or username/password fields. The
credentials file is treated as a complete NATS authentication mode.

## TLS With A Local CA Certificate

Private NATS deployments often use a private CA or self-signed development CA.
Configure `tls_ca_file` with the local CA certificate and use a `tls://` URL:

```json
{
  "nats": {
    "url": "tls://nats.internal.example:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*",
    "token_env": "NATS_TOKEN",
    "tls_ca_file": "/etc/nats/certs/root-ca.crt",
    "tls_verify": true
  }
}
```

The CLI builds an `ssl.SSLContext` with that CA file:

```mermaid
flowchart LR
    Config[config.json tls_ca_file] --> Context[ssl.create_default_context]
    Context --> Trust[Trust private CA]
    Trust --> Verify[Verify NATS server certificate]
    Verify --> Connect[nats-py connect]
```

Keep `tls_verify` set to `true` in production. Setting `tls_verify` to `false`
disables hostname and certificate verification and should be limited to
short-lived local development experiments.

For private mission networks, prefer importing the relevant CA certificate into
the service configuration over weakening TLS verification. A local CA is a
normal pattern for internal infrastructure; disabling verification should not
become the workaround for certificate lifecycle issues.

## Multiple Seed URLs

NATS clients can receive more than one server URL. This helps the client reach
a clustered deployment when one server is temporarily unavailable. Configure
`nats.urls` when you want an explicit seed list:

```json
{
  "nats": {
    "urls": [
      "tls://nats-a.internal.example:4222",
      "tls://nats-b.internal.example:4222",
      "tls://nats-c.internal.example:4222"
    ],
    "stream": "ORDERS",
    "consumer": "orders-file-sink",
    "subject": "orders.*",
    "token_env": "NATS_TOKEN",
    "tls_ca_file": "/etc/nats/certs/root-ca.crt"
  }
}
```

When `urls` is present, it is passed to `nats-py` as the `servers` option and
takes precedence over the single `url` value. Keep the single `url` field for
simple local development or single-endpoint deployments.

Every URL in `urls` is validated with the same scheme allow list as `url`:
`nats`, `tls`, `ws`, or `wss`. If any configured seed URL uses `tls://`,
`nats-sinks` builds a TLS context and passes it to `nats-py`, even when the
fallback `url` field remains at its default value.

Do not embed credentials in any URL. Use `token_env` or `password_env` so
secrets stay out of configuration files, process listings, logs, and support
bundles.

## Reconnect Tuning

Automatic reconnect is enabled by default. The defaults are intentionally close
to the `nats-py` client defaults so a basic deployment does not need to tune
anything immediately:

```json
{
  "nats": {
    "url": "tls://nats.internal.example:4222",
    "stream": "ORDERS",
    "consumer": "orders-file-sink",
    "subject": "orders.*",
    "allow_reconnect": true,
    "connect_timeout_seconds": 2,
    "reconnect_time_wait_seconds": 2,
    "max_reconnect_attempts": 60,
    "ping_interval_seconds": 120,
    "max_outstanding_pings": 2,
    "pending_size_bytes": 2097152,
    "drain_timeout_seconds": 30
  }
}
```

| Field | Passed to `nats-py` | Default | Guidance |
| --- | --- | --- | --- |
| `allow_reconnect` | `allow_reconnect` | `true` | Keep enabled for production unless a supervisor should fail fast and restart the process. |
| `connect_timeout_seconds` | `connect_timeout` | `2` | Increase when connecting across slower controlled networks. |
| `reconnect_time_wait_seconds` | `reconnect_time_wait` | `2` | Increase to reduce retry pressure during planned outages. |
| `max_reconnect_attempts` | `max_reconnect_attempts` | `60` | Use a bounded value for fail-fast service supervision, or `-1` for unlimited attempts when the process should wait for NATS to return. |
| `ping_interval_seconds` | `ping_interval` | `120` | Lower values detect broken connections sooner, at the cost of more heartbeat traffic. |
| `max_outstanding_pings` | `max_outstanding_pings` | `2` | Controls how many unanswered pings are tolerated before the client treats the connection as unhealthy. |
| `pending_size_bytes` | `pending_size` | `2097152` | Bounds client pending bytes. Increase carefully for high-throughput deployments after measuring memory growth. |
| `drain_timeout_seconds` | `drain_timeout` | `30` | Bounds client drain behavior during shutdown. |

For operational or mission-support deployments, reconnect settings should be
chosen together with service manager restart policy, JetStream consumer
`AckWait`, destination write latency, and alerting. A reconnect does not weaken
commit-then-ACK: messages that were not ACKed remain eligible for redelivery.

## Connection Event Metrics

The runner installs `nats-py` connection callbacks and increments metrics when
the client reports connection state changes:

| Metric suffix | Meaning |
| --- | --- |
| `nats_connection_disconnected_total` | The client reported a disconnect. |
| `nats_connection_reconnected_total` | The client successfully reconnected. |
| `nats_connection_closed_total` | The client reported the connection closed. |
| `nats_discovered_servers_total` | The client discovered an additional server. |
| `nats_async_errors_total` | The client reported an asynchronous connection/client error. |

These metrics appear in any configured metrics recorder, including the local
JSON snapshot inspected by `nats-sink-metrics`:

```bash
nats-sink-metrics show .local/nats-sinks/metrics.json \
  --kind counter \
  --metric "nats_*"
```

Example output:

```text
KIND     METRIC                              VALUE  DESCRIPTION
counter  nats_async_errors_total                0  NATS asynchronous error callback events observed by the runner.
counter  nats_connection_disconnected_total     1  NATS client disconnect events observed by the runner.
counter  nats_connection_reconnected_total      1  NATS client reconnect events observed by the runner.
```

Embedding applications can still provide their own `nats-py` callbacks in
`nats_options`. The runner wraps those callbacks instead of replacing them:
metrics are recorded first, then the application callback runs. If the
application callback raises, the runner logs that callback failure without
printing secrets.

## Optional Client Certificate Files

The config model includes:

```json
{
  "nats": {
    "tls_cert_file": "/etc/nats/certs/client.crt",
    "tls_key_file": "/etc/nats/private/client.key"
  }
}
```

When present, the CLI loads the certificate chain into the Python SSL context
and passes it to `nats-py`. Full TLS certificate identity mapping and
certificate-auth-specific authorization guidance are not yet certified as a
`nats-sinks` production auth mode; they are tracked on the roadmap.

## Least-Privilege NATS Permissions

Authentication proves which client is connecting. Authorization decides which
subjects that client may publish or subscribe to. Production sink workers
should use both: strong authentication for identity and narrow subject
permissions for blast-radius control.

The recommended production pattern is to pre-create the stream and durable
consumer with an administrative account, then run `nats-sinks` with a runtime
account that can only:

- request batches from `$JS.API.CONSUMER.MSG.NEXT.<STREAM>.<CONSUMER>`,
- receive request/reply responses on the configured inbox pattern,
- publish ACK/NAK responses under `$JS.ACK.<STREAM>.<CONSUMER>.>`, and
- publish to the configured DLQ subject only when DLQ is enabled.

Full templates, diagrams, and validation checklists are documented in
[NATS Least-Privilege Permissions](nats-permissions.md).

## Secret Redaction

`nats-sink show-effective-config` redacts:

- `password`,
- `password_env`,
- `token`,
- `token_env`,
- `credentials`,
- `creds`,
- URLs containing embedded credentials.

Prefer this pattern for services:

```text
/etc/nats-sinks/config.json       non-secret runtime config
/etc/nats-sinks/nats-sink.env     secret environment variables
```

## Authentication Decision Guide

```mermaid
flowchart TD
    Start[Need NATS auth?] --> Local{Local isolated dev?}
    Local -->|yes| NoAuth[No auth may be acceptable]
    Local -->|no| Simple{Simple shared secret acceptable?}
    Simple -->|yes| Token[Use token_env plus TLS]
    Simple -->|no| UserPass{Per-client identity needed?}
    UserPass -->|yes| Password[Use user plus password_env plus TLS]
    Password --> Bcrypt[Store bcrypt hash server-side when desired]
    UserPass -->|needs stronger identity| Roadmap[Track NKEY, JWT, or TLS certificate auth]
```

## Current Field Reference

| Field | Purpose | Secret? | Recommendation |
| --- | --- | --- | --- |
| `nats.url` | NATS server URL. Use `tls://` for TLS. | Sometimes | Do not embed credentials in URLs. |
| `nats.user` | Username for username/password auth. | Usually no | Use with `password_env`. |
| `nats.password` | Direct client password. | Yes | Avoid outside disposable local tests. |
| `nats.password_env` | Environment variable containing the client password. | Env var name only | Preferred for username/password auth. |
| `nats.token` | Direct client token. | Yes | Avoid outside disposable local tests. |
| `nats.token_env` | Environment variable containing the client token. | Env var name only | Preferred for token auth. |
| `nats.tls_ca_file` | Local CA certificate used to verify the NATS server. | No | Use for private or self-signed CAs. |
| `nats.tls_verify` | Enables certificate and hostname verification. | No | Keep `true` in production. |
| `nats.tls_cert_file` | Optional client certificate chain. | No, but sensitive operationally | Roadmap for certified cert auth. |
| `nats.tls_key_file` | Optional client private key file. | Yes | Protect file permissions carefully. |

## Live Connection Probe

The repository includes a tracked manual probe script:

```text
scripts/nats-live-probe.py
```

The script intentionally has no hardcoded server, username, password, token, or
CA certificate. Put real runtime material under `.local/`, which is ignored by
git.

Prepare local files:

```bash
mkdir -p .local/nats-live
chmod 700 .local/nats-live

# Save your local CA certificate here:
$EDITOR .local/nats-live/ca.crt

# Save local secrets here:
cat > .local/nats-live/nats-sink.env <<'EOF'
NATS_PASSWORD=replace-with-test-password
EOF

chmod 600 .local/nats-live/ca.crt .local/nats-live/nats-sink.env
```

Subscribe without publishing:

```bash
python scripts/nats-live-probe.py \
  --server tls://nats.example.com:4222 \
  --user example_user \
  --password-env NATS_PASSWORD \
  --env-file .local/nats-live/nats-sink.env \
  --ca-file .local/nats-live/ca.crt \
  --subject example.test.subject
```

Publish and receive a test message:

```bash
python scripts/nats-live-probe.py \
  --server tls://nats.example.com:4222 \
  --user example_user \
  --password-env NATS_PASSWORD \
  --env-file .local/nats-live/nats-sink.env \
  --ca-file .local/nats-live/ca.crt \
  --subject example.test.subject \
  --publish \
  --message '{"probe":"nats-sinks","kind":"live-test"}'
```

The probe prints connection status, subscription status, publish status, and
received payload size. It does not print payload content unless
`--print-payload` is explicitly set.

For a compact walkthrough, see the tracked
[live NATS probe example](https://github.com/ProjectCuillin/nats-sinks/tree/main/examples/nats-live).

## References

- [NATS Authentication](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro)
- [NATS Token Authentication](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro/tokens)
- [NATS TLS](https://docs.nats.io/using-nats/developer/connecting/tls)
- [NATS Authorization](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/authorization)
- [NATS NKEY Authentication](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro/nkey_auth)
- [NATS Decentralized JWT Authentication/Authorization](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/auth_intro/jwt)
