# Azure Monitor Integration

The Azure Monitor integration exports approved `nats-sinks` metrics as Azure
Monitor custom metrics for one explicitly configured Azure resource. It is
intended for Microsoft cloud deployments that already use Azure Monitor,
Metrics Explorer, alert rules, or downstream Azure observability tooling.

The connector is part of the observability plane, not the delivery plane. It
reads a local metrics snapshot, applies the shared observability policy, and
uses a bounded HTTPS request only when live export is requested. It does not
connect to NATS, does not read sink payloads, does not inspect Oracle Database,
Oracle MySQL, Oracle Coherence Community Edition, file-sink output, or other
destination records, and never affects ACK, NAK, DLQ, retry, fan-out,
idempotency, or sink-write decisions.

## Azure API Shape

Microsoft documents Azure Monitor custom metrics ingestion through a REST API
where metric data is posted to a regional Azure Monitor endpoint for a concrete
Azure resource:

- [Ingest custom metrics for an Azure resource using the REST API](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/metrics-store-custom-rest-api)
- [Metrics - Custom - Create REST API](https://learn.microsoft.com/en-us/rest/api/monitor/metrics-custom/create?view=rest-monitor-2018-09-01-preview)

Important Azure-side constraints are reflected in the local connector:

- custom metrics are emitted for a specific Azure resource ID, not a whole
  subscription or resource group;
- the regional endpoint must match the Azure resource location;
- authentication uses a Microsoft Entra bearer token for the
  `https://monitoring.azure.com/` audience;
- dimensions are optional but capped locally at 10;
- metric values are sent as pre-aggregated `min`, `max`, `sum`, and `count`
  fields.

The current implementation uses the custom metrics REST path. Operators should
evaluate Azure Monitor workspace based custom metrics separately where that is
the preferred enterprise standard, because that is a different ingestion model.

## Security Model

Azure Monitor export is disabled by default. Enable it only after reviewing the
metric allow list, the Azure resource scope, the identity used by the separate
observability service, and any dimensions.

The connector deliberately does not export:

- message payloads;
- NATS subjects;
- message IDs;
- stream names or consumer names;
- NATS server URLs;
- Oracle connection strings;
- table names;
- file paths;
- classification values;
- labels unless prepared metric labels are explicitly enabled as dimensions;
- mission metadata;
- Azure tenant IDs;
- client IDs or client secrets;
- bearer tokens;
- Azure resource IDs in dry-run output or result summaries;
- Azure regional endpoints in dry-run output or result summaries;
- exception messages from the HTTP client.

Prepared subject-family labels can be exported as Azure dimensions only when
`subject_metrics.enabled` and
`azure_monitor.include_metric_labels_as_dimensions` are both explicitly
enabled. The default is to suppress prepared labels so an Azure custom metric
does not accidentally become high-cardinality or reveal routing structure.

## Configuration

Azure Monitor configuration lives inside the same observability policy JSON as
the other connectors:

```json
{
  "schema": "nats_sinks.observability.policy.v1",
  "enabled": true,
  "namespace": "mission_ops",
  "allowed_metrics": [
    "messages_fetched_total",
    "messages_acked_total"
  ],
  "allowed_metric_patterns": [],
  "denied_metrics": [],
  "denied_metric_patterns": [],
  "include_observations": false,
  "include_legacy": false,
  "subjects": [],
  "azure_monitor": {
    "enabled": true,
    "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-observability/providers/Microsoft.Storage/storageAccounts/natssinks",
    "location": "westeurope",
    "metric_namespace": "nats-sinks/metrics",
    "token_env": "AZURE_MONITOR_BEARER_TOKEN",
    "dimensions": {
      "deployment": "edge",
      "environment": "prod"
    },
    "include_metric_labels_as_dimensions": false,
    "timeout_seconds": 5,
    "max_retries": 0,
    "retry_backoff_seconds": 0.25,
    "stale_after_seconds": 60,
    "max_request_bytes": 1048576
  }
}
```

### Options

| Option | Default | Description |
| --- | --- | --- |
| `azure_monitor.enabled` | `false` | Enables Azure Monitor export when the top-level observability policy is also enabled. |
| `azure_monitor.resource_id` | `null` | Azure resource ID that owns the custom metrics. Required when Azure Monitor export is enabled. It must identify a concrete resource below `/subscriptions/.../resourceGroups/.../providers/...`. Dry-run output does not include this value. |
| `azure_monitor.location` | `null` | Azure location for the monitored resource, such as `westeurope` or `eastus2`. Required when Azure Monitor export is enabled. Dry-run output does not include this value. |
| `azure_monitor.metric_namespace` | `nats-sinks/metrics` | Azure custom metric namespace. It must not use reserved Azure prefixes, must not contain colons, and is limited to bounded safe ASCII characters. |
| `azure_monitor.token_env` | `null` | Environment variable that contains the Microsoft Entra bearer token. Required when Azure Monitor export is enabled. The token value is never stored in policy JSON. |
| `azure_monitor.dimensions` | `{}` | Static low-cardinality dimensions added to every metric. Dimension names and values that look sensitive or high-cardinality, such as `subject`, `classification`, `label`, `message`, `table`, `file`, `host`, `user`, `subscription`, `tenant`, `token`, `secret`, or `key`, are rejected. |
| `azure_monitor.include_metric_labels_as_dimensions` | `false` | When `true`, prepared `labeled_metrics` rows can export their bounded labels as Azure dimensions. Keep disabled unless subject-family sharing has been reviewed. |
| `azure_monitor.timeout_seconds` | `5` | HTTP timeout for each Azure Monitor request. |
| `azure_monitor.max_retries` | `0` | Bounded connector-level retries after the first failed request set. |
| `azure_monitor.retry_backoff_seconds` | `0.25` | Delay between connector-level retry attempts. |
| `azure_monitor.stale_after_seconds` | `null` | Optional maximum metrics snapshot age. When set, stale snapshots fail closed unless `--allow-stale` is used. |
| `azure_monitor.max_request_bytes` | `1048576` | Maximum rendered request size. Oversized requests fail closed before any HTTP request is made. |
| `azure_monitor.verify_tls` | `true` | TLS verification is always enabled. This field is intentionally not configurable to `false`. |

## Identity

Prefer short-lived or platform-managed Azure identity:

- managed identity for Azure resources;
- workload identity in supported Kubernetes deployments;
- service principal tokens obtained by a separate protected credential process;
- Azure CLI access tokens for local operator testing only.

The token must be issued for the `https://monitoring.azure.com/` audience. One
local testing pattern is:

```bash
export AZURE_MONITOR_BEARER_TOKEN="$(az account get-access-token \
  --resource https://monitoring.azure.com/ \
  --query accessToken \
  --output tsv)"
```

Do not put tenant IDs, client secrets, access tokens, or private endpoint
values in policy files, shell history, GitHub issues, test reports, or release
evidence. The policy stores only the environment variable name.

## Dry Run

Dry-run mode renders the Azure custom metric request bodies without loading a
bearer token or calling Azure:

```bash
nats-sink-observe azure-monitor-export \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json \
  --dry-run
```

Example output:

```json
[
  {
    "data": {
      "baseData": {
        "dimNames": [
          "deployment",
          "environment"
        ],
        "metric": "mission_ops_messages_fetched_total",
        "namespace": "nats-sinks/metrics",
        "series": [
          {
            "count": 1,
            "dimValues": [
              "edge",
              "prod"
            ],
            "max": 256.0,
            "min": 256.0,
            "sum": 256.0
          }
        ]
      }
    },
    "time": "2026-05-28T12:00:00.000Z"
  }
]
```

The request body does not contain the Azure location, resource ID, bearer-token
environment variable, NATS subject, payload, classification, labels, file path,
table name, or destination address.

## Live Export

Live export reads the bearer token from `azure_monitor.token_env` and posts
the policy-approved custom metric request bodies to Azure Monitor:

```bash
nats-sink-observe azure-monitor-export \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json
```

Successful output is intentionally short:

```text
Azure Monitor export: attempted=true delivered=true attempts=1 requests=2 metrics=2 status=200 message=Azure Monitor export delivered
```

If the connector exhausts its bounded retries, the CLI exits with status `3`
and prints a sanitized category:

```text
Azure Monitor export: attempted=true delivered=false attempts=3 requests=2 metrics=2 status=none message=Azure Monitor export failed with TimeoutError
```

The message does not include tokens, tenant IDs, client IDs, resource IDs,
locations, endpoints, exception messages, payloads, or dimension values.

## Service Deployment

Run Azure Monitor export separately from the sink worker. The sink worker writes
the local metrics snapshot. The Azure Monitor service reads that snapshot and
the observability policy.

Example systemd service:

```ini
[Unit]
Description=nats-sinks Azure Monitor export
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=nats-sink-observe
Group=nats-sink-observe
EnvironmentFile=/etc/nats-sinks/azure-monitor.env
ExecStart=/usr/local/bin/nats-sink-observe azure-monitor-export /var/lib/nats-sink/metrics.json /etc/nats-sinks/observability.prometheus.json
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/nats-sink

[Install]
WantedBy=multi-user.target
```

Example timer:

```ini
[Unit]
Description=Run nats-sinks Azure Monitor export periodically

[Timer]
OnBootSec=30s
OnUnitActiveSec=30s
AccuracySec=5s

[Install]
WantedBy=timers.target
```

Keep the `EnvironmentFile` readable only by the service account and root. It
must contain a short-lived bearer token or a local command should refresh the
token before the timer runs.

## Testing

The default test suite uses fake HTTP clients and dry-run request rendering. It
does not need an Azure subscription, Microsoft Entra credentials, or live
network access.

Useful local checks:

```bash
python -m pytest tests/unit/test_azure_monitor_observability.py -q
python -m pytest tests/unit/test_observability_cli.py -q
python -m pytest tests/unit/test_public_api.py -q
```

Optional live validation should be performed only in a non-production Azure
subscription with a disposable resource, a least-privilege identity, and a
metric namespace approved for testing. Keep live details out of test reports
and issue comments.

## Limitations

The Azure Monitor connector does not:

- acquire Microsoft Entra tokens itself;
- manage Azure resources, resource groups, subscriptions, alert rules, action
  groups, workspaces, or dashboards;
- export raw subjects, payloads, table names, file paths, classifications, or
  mission metadata;
- provide a durable telemetry queue;
- guarantee Azure Monitor ingestion, retention, alert evaluation, or regional
  availability;
- participate in sink delivery success, ACK, NAK, DLQ, retry, fan-out, or
  idempotency decisions.
