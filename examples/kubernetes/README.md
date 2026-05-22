# Kubernetes Examples

These examples show one conservative way to run `nats-sinks` in Kubernetes.
They are intentionally generic and public-safe: all names, subjects, service
hosts, and secret values are placeholders. Replace them with values owned by
your cluster and security process before applying anything.

The examples keep the delivery worker and observability concerns separate:

- `sink-worker-deployment.yaml` runs only the `nats-sink` worker.
- `prometheus-http-sidecar-deployment.yaml` shows an optional second container
  that reads a local metrics snapshot and exposes policy-filtered Prometheus
  output.
- `observability-policy-configmap.yaml` keeps external metric sharing disabled
  until an operator explicitly allows selected metrics.

Runtime configuration remains JSON. The Kubernetes manifest is YAML because
that is the Kubernetes API representation, but the `config.json` stored in the
ConfigMap is the same JSON shape used everywhere else in the project.

## Files

| File | Purpose |
| --- | --- |
| `namespace.yaml` | Dedicated namespace for example resources. |
| `service-account.yaml` | Dedicated service account with no extra RBAC. |
| `configmap-file-worker.yaml` | Non-secret `config.json` for a file sink worker. |
| `secret-template.yaml` | Placeholder Secret shape for NATS, Oracle, and encryption material. |
| `persistent-volume-claim.yaml` | Example persistent volume claim for local file-sink output. |
| `sink-worker-deployment.yaml` | Delivery worker Deployment with resource limits, security context, probes, mounted TLS material, and graceful termination. |
| `observability-policy-configmap.yaml` | Disabled-by-default observability policy. |
| `prometheus-http-sidecar-deployment.yaml` | Optional worker-plus-observability-sidecar example that exposes `/metrics` only after policy review. |
| `prometheus-http-service.yaml` | ClusterIP Service for the optional native Prometheus endpoint. |
| `network-policy.yaml` | Example default-deny-style network policy skeleton to customize. |

## Safe Review Flow

Review before applying:

```bash
kubectl apply --dry-run=client -f examples/kubernetes/
```

Apply only after replacing placeholders:

```bash
kubectl apply -f examples/kubernetes/namespace.yaml
kubectl apply -f examples/kubernetes/service-account.yaml
kubectl apply -f examples/kubernetes/configmap-file-worker.yaml
kubectl apply -f examples/kubernetes/secret-template.yaml
kubectl apply -f examples/kubernetes/persistent-volume-claim.yaml
kubectl apply -f examples/kubernetes/sink-worker-deployment.yaml
```

The observability policy example is disabled by default. Enable the optional
Prometheus HTTP sidecar only after editing
`observability-policy-configmap.yaml` so both the top-level `enabled` field and
`prometheus.http_endpoint.enabled` are true, and only after reviewing the
metric allow list:

```bash
kubectl apply -f examples/kubernetes/observability-policy-configmap.yaml
kubectl apply -f examples/kubernetes/prometheus-http-sidecar-deployment.yaml
kubectl apply -f examples/kubernetes/prometheus-http-service.yaml
```

## Values To Customize

Customize at least:

- image reference and tag,
- NATS URL, stream, consumer, subject, and DLQ subject,
- sink type and destination settings,
- Secret values injected by your secret manager or deployment pipeline,
- TLS CA certificate or credentials-file material,
- output volume strategy for file sinks,
- CPU and memory requests/limits,
- namespace labels and NetworkPolicy selectors,
- Prometheus scrape path, service labels, and metric allow list.

Never commit real credentials, Oracle wallets, NATS credentials files, private
CA bundles, internal hostnames, production subjects, or sensitive payload
examples into these manifests.
