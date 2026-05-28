# Amazon CloudWatch Integration

The Amazon CloudWatch integration exports approved `nats-sinks` metrics as
CloudWatch custom metrics. It is intended for AWS deployments where CloudWatch
is the operational dashboarding and alerting surface.

The connector is part of the observability plane, not the delivery plane. It
reads a local metrics snapshot, applies the shared observability policy, and
uses the AWS SDK only when a live export is requested. It does not connect to
NATS, does not read sink payloads, does not inspect Oracle Database, Oracle
MySQL, file-sink output, or other destination records, and never affects ACK,
NAK, DLQ, retry, or sink-write decisions.

## Security Model

Amazon CloudWatch export is disabled by default. Enable it only after reviewing
which aggregate metric names may leave the host and which AWS identity will be
used by the separate observability service.

The connector deliberately does not export:

- message payloads,
- NATS subjects,
- message IDs,
- file paths,
- table names,
- Oracle connection details,
- classification values,
- message labels,
- mission metadata,
- AWS account IDs,
- AWS access keys,
- AWS secret keys,
- session tokens,
- CloudWatch endpoints,
- exception messages from the AWS SDK.

Prepared subject-family labels can be exported as CloudWatch dimensions only
when `subject_metrics.enabled` and `cloudwatch.include_metric_labels_as_dimensions`
are both explicitly enabled. The default is to suppress prepared labels so a
CloudWatch metric does not accidentally become high-cardinality or reveal
operational routing structure.

## AWS API Bounds

The connector uses the `PutMetricData` API for custom metrics:

- [Amazon CloudWatch PutMetricData](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_PutMetricData.html)
- [Amazon CloudWatch MetricDatum](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_MetricDatum.html)

AWS currently documents these important limits:

- each `PutMetricData` request is limited to 1 MB,
- each request can include no more than 1000 metrics,
- metric names are limited to 255 characters,
- AWS permits up to 30 dimensions per metric,
- special numeric values such as `NaN` and infinity are rejected.

`nats-sinks` uses a stricter default posture:

- `cloudwatch.max_metrics_per_request` defaults to `20`,
- `cloudwatch.max_request_bytes` is capped at `1048576`,
- static dimensions are capped at `10`,
- metric labels are not turned into dimensions unless explicitly enabled.

That keeps API cost, request size, and dimension cardinality visible before a
deployment goes live.

## Installation

The base package does not install boto3. Install the optional CloudWatch extra
only on hosts that will perform live CloudWatch export:

```bash
python -m pip install "nats-sinks[cloudwatch]"
```

Dry-run rendering and unit tests do not require boto3. This is intentional so
developers can inspect the request shape without AWS credentials.

## Configuration

CloudWatch configuration lives inside the same observability policy JSON as the
other connectors:

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
  "cloudwatch": {
    "enabled": true,
    "metric_namespace": "nats-sinks/metrics",
    "region": "eu-west-1",
    "unit": "None",
    "storage_resolution": 60,
    "dimensions": {
      "deployment": "edge",
      "environment": "prod"
    },
    "include_metric_labels_as_dimensions": false,
    "timeout_seconds": 5,
    "max_retries": 0,
    "retry_backoff_seconds": 0.25,
    "stale_after_seconds": 60,
    "max_metrics_per_request": 20,
    "max_request_bytes": 1048576
  }
}
```

### Options

| Option | Default | Description |
| --- | --- | --- |
| `cloudwatch.enabled` | `false` | Enables Amazon CloudWatch export when the top-level observability policy is also enabled. |
| `cloudwatch.metric_namespace` | `nats-sinks/metrics` | CloudWatch custom metric namespace. It must not start with `AWS/`, must not contain colons, and is limited to safe ASCII characters. |
| `cloudwatch.region` | `null` | AWS region used by the SDK client. Required when CloudWatch export is enabled. The dry-run output does not include this value. |
| `cloudwatch.unit` | `None` | CloudWatch metric unit. Valid values are the units accepted by `MetricDatum`, such as `Count`, `Seconds`, `Milliseconds`, `Bytes`, `Percent`, and `None`. |
| `cloudwatch.storage_resolution` | `60` | CloudWatch storage resolution. Use `60` for standard resolution or `1` for high-resolution custom metrics. |
| `cloudwatch.dimensions` | `{}` | Static low-cardinality dimensions added to every metric. Dimension names that look sensitive or high-cardinality, such as `subject`, `classification`, `label`, `message`, `table`, `file`, `host`, `user`, `token`, `secret`, or `key`, are rejected. |
| `cloudwatch.include_metric_labels_as_dimensions` | `false` | When `true`, prepared `labeled_metrics` rows can export their bounded labels as dimensions. Keep this disabled unless subject-family sharing has been reviewed. |
| `cloudwatch.timeout_seconds` | `5` | Connection and read timeout used when the boto3 client is created. |
| `cloudwatch.max_retries` | `0` | Bounded connector-level retries after the first failed `PutMetricData` attempt. |
| `cloudwatch.retry_backoff_seconds` | `0.25` | Delay between connector-level retry attempts. |
| `cloudwatch.stale_after_seconds` | `null` | Optional maximum metrics snapshot age. When set, stale snapshots fail closed unless `--allow-stale` is used. |
| `cloudwatch.max_metrics_per_request` | `20` | Maximum metric datum objects per `PutMetricData` request. The local cap cannot exceed the AWS request limit of `1000`. |
| `cloudwatch.max_request_bytes` | `1048576` | Maximum rendered request size. Oversized requests fail closed before the AWS SDK is called. |

## Dry Run

Dry-run mode renders the `PutMetricData` request list without importing boto3,
loading credentials, or calling AWS:

```bash
nats-sink-observe cloudwatch-export \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json \
  --dry-run
```

Example output:

```json
[
  {
    "MetricData": [
      {
        "Dimensions": [
          {
            "Name": "deployment",
            "Value": "edge"
          },
          {
            "Name": "environment",
            "Value": "prod"
          }
        ],
        "MetricName": "mission_ops_messages_fetched_total",
        "StorageResolution": 60,
        "Unit": "None",
        "Value": 256.0
      }
    ],
    "Namespace": "nats-sinks/metrics"
  }
]
```

Notice that the request body does not contain the AWS region, AWS account, AWS
credentials, NATS subject, payload, classification, labels, file path, table
name, or destination address.

## Live Export

Live export uses the optional boto3 path:

```bash
nats-sink-observe cloudwatch-export \
  /var/lib/nats-sink/metrics.json \
  /etc/nats-sinks/observability.prometheus.json
```

Successful output is intentionally short:

```text
Amazon CloudWatch export: attempted=true delivered=true attempts=1 requests=1 metrics=2 message=Amazon CloudWatch export delivered
```

If the connector exhausts its bounded retries, the CLI exits with status `3`
and prints a sanitized category:

```text
Amazon CloudWatch export: attempted=true delivered=false attempts=3 requests=1 metrics=2 message=Amazon CloudWatch export failed with TimeoutError
```

The message does not include AWS account IDs, endpoints, regions, access keys,
request IDs, exception messages, or metric payload details.

## AWS Identity And IAM

Prefer short-lived or platform-managed AWS identity:

- EC2 instance profiles,
- ECS task roles,
- EKS workload identity or IRSA,
- AWS SSO profiles for local operator testing,
- environment-backed credentials only for short-lived local checks.

Do not store AWS access keys in the observability policy. Do not pass access
keys through command-line arguments. The observability service should have only
the permissions required to call `cloudwatch:PutMetricData` for the approved
namespace and should not receive broad CloudWatch, IAM, or account-management
permissions.

## Subject-Family Dimensions

Prepared subject-family metrics are disabled by default. If an approved policy
has already generated safe `labeled_metrics` rows, CloudWatch can turn those
prepared labels into dimensions:

```json
{
  "subject_metrics": {
    "enabled": true,
    "rules": [
      {
        "subject": "sensor.>",
        "action": "allow",
        "label": "sensor_track"
      }
    ]
  },
  "cloudwatch": {
    "enabled": true,
    "metric_namespace": "nats-sinks/metrics",
    "region": "eu-west-1",
    "include_metric_labels_as_dimensions": true
  }
}
```

With the flag enabled, a prepared row can render like this:

```json
{
  "MetricName": "mission_ops_messages_fetched_total",
  "Value": 4.0,
  "Unit": "None",
  "StorageResolution": 60,
  "Dimensions": [
    {
      "Name": "subject_family",
      "Value": "sensor_track"
    }
  ]
}
```

Do not enable this for raw subject names. Use stable family labels, redacted
labels, or hashed labels from the subject-aware observability runbook.

## Service Separation

Run CloudWatch export separately from the sink worker. The sink worker writes
the local metrics snapshot. The CloudWatch service reads that snapshot and the
observability policy.

Example oneshot unit:

```ini
[Unit]
Description=nats-sinks Amazon CloudWatch export
After=network-online.target

[Service]
Type=oneshot
User=nats-sinks-observe
Group=nats-sinks
ExecStart=/usr/local/bin/nats-sink-observe cloudwatch-export /var/lib/nats-sink/metrics.json /etc/nats-sinks/observability.prometheus.json
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/nats-sink
```

For periodic export, use a systemd timer. Keep AWS credentials scoped to the
observability service identity, not the delivery worker, whenever practical.

## Testing

The default test suite uses fake CloudWatch clients and dry-run request
rendering. It does not require AWS credentials:

```bash
python -m pytest tests/unit/test_cloudwatch_observability.py tests/unit/test_observability_cli.py -q
```

The focused tests cover:

- disabled-by-default behavior,
- shared allow-list and deny-list filtering,
- observation inclusion controls,
- static dimension validation,
- prepared label suppression by default,
- explicit prepared labels as dimensions,
- request chunking,
- local request-size failure,
- bounded retry behavior,
- sanitized failure summaries,
- CLI dry-run output,
- stale snapshot rejection.

Live AWS integration testing should use a disposable AWS account or controlled
lab account, a short-lived role, an approved namespace, and a narrow metric
allow list. Do not run live CloudWatch tests from the default unit suite.

## Limitations

The first CloudWatch connector intentionally does not:

- publish logs,
- create dashboards,
- create alarms,
- manage CloudWatch Metric Streams,
- create or manage IAM roles,
- export raw NATS subjects,
- export raw message labels,
- export classification or mission metadata values,
- guarantee CloudWatch ingestion, retention, or alert evaluation.

It provides a bounded custom-metric export path for approved aggregate
`nats-sinks` metrics only.
