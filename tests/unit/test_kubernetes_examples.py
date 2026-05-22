# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the public Kubernetes deployment examples.

The examples must remain useful without requiring a Kubernetes cluster during
CI.  These tests inspect the tracked manifests and documentation text directly
so accidental removal of security controls, graceful shutdown settings, or
observability separation is caught before release.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EXAMPLE_DIR = Path("examples/kubernetes")


def _read(name: str) -> str:
    return (EXAMPLE_DIR / name).read_text(encoding="utf-8")


def _extract_configmap_json(manifest: str) -> dict[str, object]:
    marker = "  config.json: |\n"
    assert marker in manifest
    after_marker = manifest.split(marker, maxsplit=1)[1]
    json_lines = [line[4:] for line in after_marker.splitlines() if line.startswith("    ")]
    return json.loads("\n".join(json_lines))


def test_kubernetes_example_manifest_set_is_present() -> None:
    expected_files = {
        "README.md",
        "namespace.yaml",
        "service-account.yaml",
        "configmap-file-worker.yaml",
        "secret-template.yaml",
        "persistent-volume-claim.yaml",
        "sink-worker-deployment.yaml",
        "observability-policy-configmap.yaml",
        "prometheus-http-sidecar-deployment.yaml",
        "prometheus-http-service.yaml",
        "network-policy.yaml",
    }

    assert expected_files <= {path.name for path in EXAMPLE_DIR.iterdir()}


def test_kubernetes_worker_configmap_contains_json_runtime_config() -> None:
    manifest = _read("configmap-file-worker.yaml")
    config = _extract_configmap_json(manifest)
    nats = config["nats"]
    delivery = config["delivery"]
    metrics = config["metrics"]
    sink = config["sink"]

    assert isinstance(nats, dict)
    assert isinstance(delivery, dict)
    assert isinstance(metrics, dict)
    assert isinstance(sink, dict)

    assert nats["password_env"] == "_".join(("NATS", "PASSWORD"))
    assert nats["tls_verify"] is True
    assert delivery["ack_policy"] == "after_sink_commit"
    assert metrics["snapshot_file"] == "/var/lib/nats-sinks/metrics/metrics.json"
    assert sink["type"] == "file"
    assert sink["duplicate_policy"] == "skip_existing"


def test_kubernetes_worker_deployment_keeps_security_and_shutdown_controls() -> None:
    manifest = _read("sink-worker-deployment.yaml")

    for required in (
        "serviceAccountName: nats-sink-worker",
        "automountServiceAccountToken: false",
        "terminationGracePeriodSeconds: 90",
        "runAsNonRoot: true",
        "allowPrivilegeEscalation: false",
        "readOnlyRootFilesystem: true",
        "seccompProfile:",
        'drop:\n                - "ALL"',
        "resources:",
        "requests:",
        "limits:",
        "readinessProbe:",
        "livenessProbe:",
        "preStop:",
        "secretKeyRef:",
        "persistentVolumeClaim:",
    ):
        assert required in manifest

    assert "nats-sink" in manifest
    assert '"/etc/nats-sinks/config.json"' in manifest


def test_kubernetes_observability_sidecar_is_separate_and_disabled_by_default() -> None:
    sidecar = _read("prometheus-http-sidecar-deployment.yaml")
    policy = _read("observability-policy-configmap.yaml")
    service = _read("prometheus-http-service.yaml")

    assert "name: nats-sink" in sidecar
    assert "name: nats-sink-observe" in sidecar
    assert "prometheus-http" in sidecar
    assert "readOnly: true" in sidecar
    assert "containerPort: 9108" in sidecar
    assert '"enabled": false' in policy
    assert '"http_endpoint"' in policy
    assert "kind: Service" in service
    assert "targetPort: metrics" in service


def test_kubernetes_examples_avoid_private_operational_values() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in EXAMPLE_DIR.iterdir())

    assert "louwersj@gmail.com" not in combined
    assert "abc123" not in combined.lower()
    assert "192.168." not in combined
    assert "adb.oraclecloud.com" not in combined
    assert "-----BEGIN" not in combined
    assert "REPLACE_IN_PRODUCTION" not in combined
    assert re.search(r"\b(?:10|172\.(?:1[6-9]|2\d|3[0-1]))\.\d+\.\d+\.\d+\b", combined) is None


def test_kubernetes_documentation_is_linked_from_public_docs() -> None:
    mkdocs = Path("mkdocs.yml").read_text(encoding="utf-8")
    operations = Path("docs/operations.md").read_text(encoding="utf-8")
    security = Path("docs/security.md").read_text(encoding="utf-8")
    observability = Path("docs/observability.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Kubernetes Deployment: kubernetes.md" in mkdocs
    assert "(kubernetes.md)" in operations
    assert "(kubernetes.md)" in security
    assert "(kubernetes.md)" in observability
    assert "https://nats-sinks.readthedocs.io/en/latest/kubernetes/" in readme
