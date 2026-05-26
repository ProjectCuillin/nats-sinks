# Prometheus Integration

`nats-sinks` supports Prometheus through two policy-controlled connectors:

- the recommended textfile connector for node_exporter's textfile collector,
- an optional native HTTP scrape endpoint for deployments that need a direct
  Prometheus target.

Both connectors read a local metrics snapshot, filter it through the same
observability policy, and expose only approved metric names. Neither connector
connects to NATS, connects to Oracle, reads file sink output, inspects
payloads, or participates in ACK decisions. They can run as separate Linux
services with narrower permissions than the main sink worker.

## Connector Choice

| Connector | Default | Best Fit | Operational Boundary |
| --- | --- | --- | --- |
| Prometheus textfile | Disabled | Hosts that already run node_exporter, environments that prefer no additional application listener, and high-trust deployments that want very small network exposure. | A timer writes a local `.prom` file; Prometheus scrapes node_exporter. |
| Native HTTP endpoint | Disabled | Environments where Prometheus is configured to scrape application services directly and operators can control listener exposure. | A separate `nats-sink-observe prometheus-http` service reads the snapshot and serves `/metrics`. |

The textfile connector remains the conservative default because it does not
open a network port. The native endpoint is useful, but it should be bound to
loopback or protected by platform network controls unless the operational
environment explicitly approves broader exposure.

## Recommended Textfile Deployment Model

```mermaid
flowchart TB
    subgraph Sink Host
        Worker[nats-sink service]
        Snapshot[/var/lib/nats-sink/metrics.json]
        Policy[/etc/nats-sinks/observability.prometheus.json]
        Timer[nats-sink-prometheus-textfile.timer]
        Export[nats-sink-prometheus-textfile.service]
        Textfile[/var/lib/node_exporter/textfile_collector/nats_sinks.prom]
        Node[node_exporter]
    end

    Prom[Prometheus Server]

    Worker --> Snapshot
    Timer --> Export
    Policy --> Export
    Snapshot --> Export
    Export --> Textfile
    Node --> Textfile
    Prom --> Node
```

The sink service and the Prometheus export service should be treated as two
separate services:

- `nats-sink.service` owns message movement and writes `metrics.json`,
- `nats-sink-prometheus-textfile.service` owns policy-filtered Prometheus
  output,
- `nats-sink-prometheus-textfile.timer` decides how often the textfile is
  refreshed,
- node_exporter owns the scrape endpoint that Prometheus reads.

## Optional Native Endpoint Deployment Model

```mermaid
flowchart TB
    subgraph Sink Host
        Worker[nats-sink service]
        Snapshot[/var/lib/nats-sink/metrics.json]
        Policy[/etc/nats-sinks/observability.prometheus.json]
        Http[nats-sink-prometheus-http.service]
    end

    Prom[Prometheus Server]

    Worker --> Snapshot
    Policy --> Http
    Snapshot --> Http
    Prom -->|GET /metrics| Http
```

The native endpoint is also a separate service. It should not run inside the
delivery-critical sink worker unless an embedding application explicitly
accepts that operational coupling.

## Enable Metrics Snapshot Writing

The Prometheus connector reads the local JSON snapshot written by
`JsonFileMetrics`. Enable it in the normal runtime config:

```json
{
  "metrics": {
    "enabled": true,
    "namespace": "nats_sinks",
    "snapshot_file": "/var/lib/nats-sink/metrics.json"
  }
}
```

This does not share anything with Prometheus by itself. It only creates a local
file for local tools and approved connectors.

## Generate A Disabled Prometheus Policy

Generate a policy from the core runtime config:

```bash
sudo /opt/nats-sinks/venv/bin/nats-sink-observe init-prometheus-policy \
  /etc/nats-sinks/config.json \
  /etc/nats-sinks/observability.prometheus.json \
  --output-file /var/lib/node_exporter/textfile_collector/nats_sinks.prom
```

The generated policy is disabled:

```json
{
  "schema": "nats_sinks.observability.policy.v1",
  "enabled": false,
  "namespace": "nats_sinks",
  "allowed_metrics": [],
  "allowed_metric_patterns": [],
  "denied_metrics": [],
  "denied_metric_patterns": [],
  "include_observations": false,
  "include_legacy": false,
  "subjects": [
    {
      "subject": "orders.*",
      "enabled": false,
      "allowed_metrics": [],
      "allowed_metric_patterns": [],
      "share_subject_label": false
    }
  ],
  "prometheus": {
    "enabled": false,
    "output_file": "/var/lib/node_exporter/textfile_collector/nats_sinks.prom",
    "include_help": true,
    "include_type": true,
    "http_endpoint": {
      "enabled": false,
      "host": "127.0.0.1",
      "port": 9108,
      "path": "/metrics",
      "request_timeout_seconds": 5,
      "response_max_bytes": 1048576
    }
  }
}
```

The `subjects` section helps operators review which subject patterns the sink
configuration knows about. Current Prometheus output does not include subject
labels by default.

Subject-aware Prometheus export has been evaluated as future work, but it is
not enabled today. Do not add raw NATS subjects as Prometheus labels through
local patches or ad hoc exporters. A future implementation needs explicit
subject-family allow rules, stable low-cardinality labels, cardinality caps,
and tests proving delivery behavior is unchanged. See
[Subject-Aware Observability Evaluation](subject-aware-observability-evaluation.md).

## Enable A Minimal Export

Edit `/etc/nats-sinks/observability.prometheus.json` and enable only the
metrics needed for operations:

```json
{
  "schema": "nats_sinks.observability.policy.v1",
  "enabled": true,
  "namespace": "nats_sinks",
  "allowed_metrics": [
    "messages_fetched_total",
    "messages_written_total",
    "messages_acked_total",
    "messages_failed_total",
    "messages_dlq_total",
    "sink_write_errors_total",
    "last_sink_success_epoch_seconds"
  ],
  "allowed_metric_patterns": [
    "nats_connection_*"
  ],
  "denied_metrics": [],
  "denied_metric_patterns": [],
  "include_observations": false,
  "include_legacy": false,
  "subjects": [],
  "prometheus": {
    "enabled": true,
    "output_file": "/var/lib/node_exporter/textfile_collector/nats_sinks.prom",
    "include_help": true,
    "include_type": true,
    "stale_after_seconds": 60,
    "http_endpoint": {
      "enabled": false,
      "host": "127.0.0.1",
      "port": 9108,
      "path": "/metrics",
      "request_timeout_seconds": 5,
      "response_max_bytes": 1048576
    }
  }
}
```

This exports core counters, the last successful sink timestamp, and NATS
connection-event counters. It does not export timings, legacy aliases, subject
names, message IDs, table names, file paths, classification values, labels, or
payload contents.

## Export Freshness Metrics

Freshness metrics can show delayed feeds, stale replay, missing publisher
timestamps, malformed `Nats-Time-Stamp` headers, and positive source clock skew.
They are aggregate metrics only; the Prometheus connector does not add subject,
source, sensor, sink, table, priority, classification, or label dimensions.

Enable the freshness counters and observations only when that timing evidence is
approved for the deployment:

```json
{
  "allowed_metric_patterns": [
    "event_*",
    "events_*"
  ],
  "include_observations": true
}
```

Example Prometheus text:

```text
# HELP nats_sinks_events_stale_at_receive_total Events older than the configured stale threshold at runner receive time.
# TYPE nats_sinks_events_stale_at_receive_total counter
nats_sinks_events_stale_at_receive_total 3
# HELP nats_sinks_event_age_at_receive_seconds Observed event age in seconds when the runner received the message.
# TYPE nats_sinks_event_age_at_receive_seconds summary
nats_sinks_event_age_at_receive_seconds_count 256
nats_sinks_event_age_at_receive_seconds_max 12.428
```

Fan-out metrics can be shared the same way when a deployment needs evidence
about multi-destination custody without exposing route or sink details:

```json
{
  "enabled": true,
  "allowed_metrics": [
    "fanout_messages_routed_total",
    "fanout_required_child_success_total",
    "fanout_required_child_failure_total",
    "fanout_optional_child_timeout_total",
    "fanout_messages_acked_total",
    "fanout_messages_ack_blocked_total",
    "fanout_ack_gate_wait_seconds"
  ],
  "prometheus": {
    "enabled": true
  }
}
```

Prometheus receives only the approved aggregate names:

```text
# HELP nats_sinks_fanout_required_child_failure_total Required fan-out child sink operations that failed and blocked ACK.
# TYPE nats_sinks_fanout_required_child_failure_total counter
nats_sinks_fanout_required_child_failure_total 1
# HELP nats_sinks_fanout_ack_gate_wait_seconds Elapsed seconds spent waiting at the fan-out ACK gate.
# TYPE nats_sinks_fanout_ack_gate_wait_seconds summary
nats_sinks_fanout_ack_gate_wait_seconds_count 4
```

## Enable The Native HTTP Endpoint

Use the native endpoint only when a direct scrape target is operationally
preferred. The textfile connector can remain disabled while the HTTP endpoint
is enabled:

```json
{
  "schema": "nats_sinks.observability.policy.v1",
  "enabled": true,
  "namespace": "nats_sinks",
  "allowed_metrics": [
    "messages_fetched_total",
    "messages_written_total",
    "messages_acked_total",
    "messages_failed_total",
    "last_sink_success_epoch_seconds"
  ],
  "allowed_metric_patterns": [
    "nats_connection_*"
  ],
  "denied_metrics": [],
  "denied_metric_patterns": [],
  "include_observations": false,
  "include_legacy": false,
  "subjects": [],
  "prometheus": {
    "enabled": false,
    "include_help": true,
    "include_type": true,
    "stale_after_seconds": 60,
    "http_endpoint": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 9108,
      "path": "/metrics",
      "request_timeout_seconds": 5,
      "response_max_bytes": 1048576
    }
  }
}
```

Preview the response without opening a listener:

```bash
nats-sink-observe prometheus-http \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json \
  --dry-run
```

Example output:

```text
# HELP nats_sinks_messages_fetched_total Raw JetStream messages fetched by the pull consumer.
# TYPE nats_sinks_messages_fetched_total counter
nats_sinks_messages_fetched_total 256
# HELP nats_sinks_messages_acked_total Messages acknowledged to JetStream after durable success or DLQ success.
# TYPE nats_sinks_messages_acked_total counter
nats_sinks_messages_acked_total 256
```

Start the endpoint:

```bash
nats-sink-observe prometheus-http \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json
```

Example output:

```text
Serving Prometheus metrics on 127.0.0.1:9108/metrics
```

Prometheus scrape configuration:

```yaml
scrape_configs:
  - job_name: "nats-sinks"
    metrics_path: "/metrics"
    static_configs:
      - targets:
          - "127.0.0.1:9108"
```

When `stale_after_seconds` is set and the local snapshot is too old, the
endpoint fails closed with a small service-unavailable response unless
`--allow-stale` is provided for a diagnostic run. If the rendered response
would exceed `response_max_bytes`, the endpoint suppresses the response instead
of sending unbounded output.

## Export Oracle Duplicate Counters

Oracle duplicate and conflict counters can be shared without exposing row data:

```json
{
  "allowed_metric_patterns": [
    "oracle_*"
  ]
}
```

Example Prometheus text:

```text
# HELP nats_sinks_oracle_duplicates_total Oracle rows identified as duplicate prior processing through idempotent handling.
# TYPE nats_sinks_oracle_duplicates_total counter
nats_sinks_oracle_duplicates_total 7
# HELP nats_sinks_oracle_duplicate_ignored_total Oracle duplicate rows safely ignored by insert_ignore mode.
# TYPE nats_sinks_oracle_duplicate_ignored_total counter
nats_sinks_oracle_duplicate_ignored_total 7
```

## Export Timing Observations

Timing observations are disabled by policy unless `include_observations` is
`true`. Enable them only when the operational value outweighs the information
shared about write latency:

```json
{
  "allowed_metrics": [
    "sink_batch_write_seconds"
  ],
  "include_observations": true
}
```

Example output:

```text
# HELP nats_sinks_sink_batch_write_seconds Elapsed seconds spent inside sink.write_batch for successful batches.
# TYPE nats_sinks_sink_batch_write_seconds summary
nats_sinks_sink_batch_write_seconds_count 4
nats_sinks_sink_batch_write_seconds_sum 3.684471
nats_sinks_sink_batch_write_seconds_min 0.812345
nats_sinks_sink_batch_write_seconds_max 1.03421
nats_sinks_sink_batch_write_seconds_last 0.901234
```

## Render Manually

Render to stdout:

```bash
nats-sink-observe prometheus-textfile \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json \
  --dry-run
```

Write to the policy `output_file`:

```bash
nats-sink-observe prometheus-textfile \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json
```

Write to an explicit path:

```bash
nats-sink-observe prometheus-textfile \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json \
  --output /var/lib/node_exporter/textfile_collector/nats_sinks.prom
```

If the policy is disabled, the command does not need a snapshot and writes only
a comment:

```text
# nats-sinks Prometheus export disabled by observability policy
```

## Linux Service Setup

From a checkout, the unified helper script detects Debian-family systems and
Oracle Linux by reading `/etc/os-release`, installs the sink service assets,
observability policy example, Prometheus textfile service/timer, optional
native Prometheus HTTP service, and the disabled NATS server monitoring
service/timer:

```bash
sudo scripts/install-systemd.sh
```

The older distribution-specific script names remain as compatibility wrappers,
but new documentation and automation should use `scripts/install-systemd.sh`.

The installer can also run as a standalone GitHub-downloaded script. In that
mode it downloads the Prometheus policy example, main service unit, textfile
service unit, textfile timer, native HTTP service unit, and NATS monitoring
service assets from the GitHub ref named by `NATS_SINKS_INSTALL_REF`.
Release-tagged installs automatically install the matching PyPI package version
unless `NATS_SINKS_PACKAGE_SPEC` is set. This is useful when the service host is
not a development checkout and should still receive a complete, pinned set of
service assets.

For a Debian-family host, the single-command GitHub install is:

```bash
curl -fsSL https://raw.githubusercontent.com/ProjectCuillin/nats-sinks/main/scripts/install-systemd.sh | sudo env NATS_SINKS_INSTALL_REF=main sh
```

For an Oracle Linux host, use the same command. The installer detects Oracle
Linux and selects `dnf` automatically:

```bash
curl -fsSL https://raw.githubusercontent.com/ProjectCuillin/nats-sinks/main/scripts/install-systemd.sh | sudo env NATS_SINKS_INSTALL_REF=main sh
```

For release-pinned production automation after this script is present in a
tagged release, replace `main` in both places with the release tag:

```bash
curl -fsSL https://raw.githubusercontent.com/ProjectCuillin/nats-sinks/vX.Y.Z/scripts/install-systemd.sh | sudo env NATS_SINKS_INSTALL_REF=vX.Y.Z sh
```

For Oracle deployments that need the optional Oracle driver dependency, pass an
explicit package spec:

```bash
curl -fsSL https://raw.githubusercontent.com/ProjectCuillin/nats-sinks/vX.Y.Z/scripts/install-systemd.sh | sudo env NATS_SINKS_INSTALL_REF=vX.Y.Z NATS_SINKS_PACKAGE_SPEC='nats-sinks[oracle]==X.Y.Z' sh
```

In high-trust environments, download and inspect the script first, then run it
with `sudo`.

## Debian Manual Service Setup

Manual Debian steps:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip prometheus-node-exporter
sudo useradd --system --home-dir /var/lib/nats-sink --create-home --shell /usr/sbin/nologin nats-sink
sudo install -d -o nats-sink -g nats-sink /var/lib/nats-sink
sudo install -d -o nats-sink -g nats-sink /var/lib/node_exporter/textfile_collector
sudo install -d /etc/nats-sinks /opt/nats-sinks
sudo python3 -m venv /opt/nats-sinks/venv
sudo /opt/nats-sinks/venv/bin/python -m pip install --upgrade pip
sudo /opt/nats-sinks/venv/bin/python -m pip install nats-sinks
sudo install -m 0640 -o root -g nats-sink examples/systemd/observability.prometheus.json /etc/nats-sinks/observability.prometheus.json
sudo install -m 0644 examples/systemd/nats-sink-prometheus-textfile.service /etc/systemd/system/nats-sink-prometheus-textfile.service
sudo install -m 0644 examples/systemd/nats-sink-prometheus-textfile.timer /etc/systemd/system/nats-sink-prometheus-textfile.timer
sudo install -m 0644 examples/systemd/nats-sink-prometheus-http.service /etc/systemd/system/nats-sink-prometheus-http.service
sudo install -m 0644 examples/systemd/nats-sink-nats-monitoring.service /etc/systemd/system/nats-sink-nats-monitoring.service
sudo install -m 0644 examples/systemd/nats-sink-nats-monitoring.timer /etc/systemd/system/nats-sink-nats-monitoring.timer
sudo systemctl daemon-reload
```

Enable the timer only after the policy has been reviewed and explicitly
enabled:

```bash
sudo systemctl enable --now nats-sink-prometheus-textfile.timer
```

Configure node_exporter to read the textfile directory. On many Debian systems,
this is done by adding the collector directory to the node_exporter defaults
file, depending on the package:

```text
ARGS="--collector.textfile.directory=/var/lib/node_exporter/textfile_collector"
```

Then restart node_exporter:

```bash
sudo systemctl restart prometheus-node-exporter
```

## Oracle Linux Manual Service Setup

Manual Oracle Linux steps:

```bash
sudo dnf install -y python3 python3-pip
sudo useradd --system --home-dir /var/lib/nats-sink --create-home --shell /sbin/nologin nats-sink
sudo install -d -o nats-sink -g nats-sink /var/lib/nats-sink
sudo install -d -o nats-sink -g nats-sink /var/lib/node_exporter/textfile_collector
sudo install -d /etc/nats-sinks /opt/nats-sinks
sudo python3 -m venv /opt/nats-sinks/venv
sudo /opt/nats-sinks/venv/bin/python -m pip install --upgrade pip
sudo /opt/nats-sinks/venv/bin/python -m pip install nats-sinks
sudo install -m 0640 -o root -g nats-sink examples/systemd/observability.prometheus.json /etc/nats-sinks/observability.prometheus.json
sudo install -m 0644 examples/systemd/nats-sink-prometheus-textfile.service /etc/systemd/system/nats-sink-prometheus-textfile.service
sudo install -m 0644 examples/systemd/nats-sink-prometheus-textfile.timer /etc/systemd/system/nats-sink-prometheus-textfile.timer
sudo install -m 0644 examples/systemd/nats-sink-prometheus-http.service /etc/systemd/system/nats-sink-prometheus-http.service
sudo install -m 0644 examples/systemd/nats-sink-nats-monitoring.service /etc/systemd/system/nats-sink-nats-monitoring.service
sudo install -m 0644 examples/systemd/nats-sink-nats-monitoring.timer /etc/systemd/system/nats-sink-nats-monitoring.timer
sudo systemctl daemon-reload
```

Install and configure node_exporter according to the packaging standard used in
your Oracle Linux environment. The required node_exporter flag is:

```text
--collector.textfile.directory=/var/lib/node_exporter/textfile_collector
```

Enable the timer after policy review:

```bash
sudo systemctl enable --now nats-sink-prometheus-textfile.timer
```

Enable the native endpoint service only after the policy has been reviewed and
`prometheus.http_endpoint.enabled` has been set to `true`:

```bash
sudo systemctl enable --now nats-sink-prometheus-http.service
```

Enable the NATS server monitoring timer only after
`nats_server_monitoring.enabled` has been set to `true`, endpoint and field
allow lists have been reviewed, and the monitoring listener is reachable only
from approved hosts:

```bash
sudo systemctl enable --now nats-sink-nats-monitoring.timer
```

## Systemd Units

The Prometheus textfile service is a short-lived oneshot unit:

```ini
[Service]
Type=oneshot
User=nats-sink
Group=nats-sink
ExecStart=/opt/nats-sinks/venv/bin/nats-sink-observe prometheus-textfile /var/lib/nats-sink/metrics.json /etc/nats-sinks/observability.prometheus.json --output /var/lib/node_exporter/textfile_collector/nats_sinks.prom
WorkingDirectory=/var/lib/nats-sink
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=/etc/nats-sinks /var/lib/nats-sink
ReadWritePaths=/var/lib/node_exporter/textfile_collector
```

The timer runs it periodically:

```ini
[Timer]
OnBootSec=30s
OnUnitActiveSec=15s
AccuracySec=5s
Persistent=true
```

The timer is intentionally independent from `nats-sink.service`. If Prometheus
export fails, message delivery should continue. Operators should alert on stale
or missing textfiles rather than coupling observability failure to message ACKs.

The native endpoint unit is long-running and reads the same local snapshot:

```ini
[Service]
Type=simple
User=nats-sink
Group=nats-sink
ExecStart=/opt/nats-sinks/venv/bin/nats-sink-observe prometheus-http /var/lib/nats-sink/metrics.json /etc/nats-sinks/observability.prometheus.json
WorkingDirectory=/var/lib/nats-sink
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=/etc/nats-sinks /var/lib/nats-sink
```

This service is installed by the helper script but not enabled. It remains
inactive unless an operator enables both the policy and the systemd unit.

## Prometheus Scrape Configuration

With the recommended textfile deployment, Prometheus scrapes node_exporter, not
`nats-sinks` directly:

```yaml
scrape_configs:
  - job_name: "node"
    static_configs:
      - targets:
          - "sink-host.example.mil:9100"
```

For a local lab:

```yaml
scrape_configs:
  - job_name: "node"
    static_configs:
      - targets:
          - "localhost:9100"
```

Prometheus will receive the `nats_sinks_*` metrics through node_exporter's
textfile collector.

With the native endpoint deployment, Prometheus scrapes the separate
`nats-sink-observe prometheus-http` process:

```yaml
scrape_configs:
  - job_name: "nats-sinks"
    static_configs:
      - targets:
          - "sink-host.example.invalid:9108"
```

Use deployment-level access control for that target. The native endpoint has no
application authentication layer in this release.

## Security Checklist

Before enabling Prometheus export, confirm:

- the main sink config enables `metrics.snapshot_file`,
- the observability policy is explicitly enabled,
- `prometheus.enabled` is explicitly enabled,
- or `prometheus.http_endpoint.enabled` is explicitly enabled when using the
  native endpoint,
- `nats_server_monitoring.prometheus_enabled` is explicitly enabled only when
  selected NATS monitoring numeric values are approved for sharing,
- the allow list contains only metrics approved for the deployment,
- `include_observations` is enabled only if timing values are safe to share,
- freshness metrics are enabled only when event-age and clock-skew timing is
  approved for the deployment,
- subject labels are not exported,
- textfile directory permissions allow the observability service to write and
  node_exporter to read,
- the metrics snapshot and textfile are not tracked in git,
- Prometheus access is restricted to authorized operators.

For restricted, defence, or coalition environments, treat Prometheus as an
operational data consumer. Even counters can reveal activity and failure
patterns.

## What Prometheus Does Not Receive By Default

The Prometheus connector does not export:

- message payloads,
- decrypted payloads,
- NATS headers,
- subject labels,
- message IDs,
- stream sequence values,
- Oracle table names,
- file sink paths,
- classification values,
- label values,
- priority values,
- usernames,
- passwords,
- tokens,
- private keys,
- certificate contents,
- full connection strings.

The separate NATS server monitoring connector also does not export raw
monitoring JSON, monitoring base URLs, account names, stream names, consumer
names, or server topology by default. It renders only numeric values selected
through `nats_server_monitoring.allowed_fields`.

This bare-minimum posture is intentional. Add only the metrics your operations
team needs.
