# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Public API compatibility checks for documented import paths.

These tests are intentionally small but important.  README examples,
documentation snippets, and PyPI users depend on import paths staying stable
while the project evolves internally.  The contract below is therefore a
release guard: adding a new public symbol is easy, but removing or moving an
existing documented symbol should be treated as a compatibility decision.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any, cast

import typer

import nats_sinks
from nats_sinks import (
    JetStreamSinkRunner as ReadmeJetStreamSinkRunner,
)
from nats_sinks import (
    NatsEnvelope as ReadmeNatsEnvelope,
)
from nats_sinks import (
    Sink as ReadmeSink,
)
from nats_sinks.file import FileSink as ReadmeFileSink
from nats_sinks.oracle import OracleSink as ReadmeOracleSink

PUBLIC_API_CONTRACT: dict[str, tuple[str, ...]] = {
    "nats_sinks": (
        "AckError",
        "ConfigurationError",
        "CUSTODY_SCHEMA",
        "CUSTODY_SUPPORTED_ALGORITHMS",
        "CustodyConfig",
        "DeadLetterError",
        "DestinationUnavailableError",
        "EncryptionConfig",
        "EncryptionRuleConfig",
        "FileSink",
        "FlushableSink",
        "HealthCheckableSink",
        "InMemoryMetrics",
        "JetStreamSinkRunner",
        "JsonFileMetrics",
        "MessageMetadataConfig",
        "MessageMetadataFieldConfig",
        "MessageMetadataLabelsConfig",
        "MessageMetadataRuleConfig",
        "MissionMetadataConfig",
        "MissionMetadataRuleConfig",
        "MetricNames",
        "NatsEnvelope",
        "NatsSinksError",
        "NoopMetrics",
        "NormalizedPayload",
        "PayloadEncryptor",
        "PayloadKeyRegistry",
        "PermanentSinkError",
        "PolicyEvaluation",
        "PolicyViolation",
        "PolicyViolationError",
        "PreSinkPolicyConfig",
        "PreSinkPolicyRuleConfig",
        "SchemaAwareSink",
        "SerializationError",
        "Sink",
        "SinkError",
        "SubjectPayloadEncryptor",
        "TemporarySinkError",
        "ValidationError",
        "attach_custody_metadata",
        "build_nats_metadata_snapshot",
        "canonical_json_bytes",
        "compute_custody_metadata",
        "datetime_to_epoch_ns",
        "decrypt_payload",
        "evaluate_pre_sink_policy",
        "is_encrypted_payload_envelope",
        "load_metrics_snapshot",
        "metric_rows_from_snapshot",
        "normalize_payload_for_json_storage",
        "parse_mission_metadata_header",
        "qualified_metric_name",
        "write_metrics_snapshot",
    ),
    "nats_sinks.core": (
        "InMemoryMetrics",
        "CUSTODY_SCHEMA",
        "CUSTODY_SUPPORTED_ALGORITHMS",
        "JetStreamSinkRunner",
        "JsonFileMetrics",
        "MetricNames",
        "NatsEnvelope",
        "NoopMetrics",
        "PayloadEncryptor",
        "PayloadKeyRegistry",
        "PolicyEvaluation",
        "PolicyViolation",
        "SubjectPayloadEncryptor",
        "attach_custody_metadata",
        "build_nats_metadata_snapshot",
        "canonical_json_bytes",
        "compute_custody_metadata",
        "datetime_to_epoch_ns",
        "decrypt_payload",
        "evaluate_pre_sink_policy",
        "is_encrypted_payload_envelope",
        "load_metrics_snapshot",
        "metric_rows_from_snapshot",
        "normalize_payload_for_json_storage",
        "parse_mission_metadata_header",
        "qualified_metric_name",
        "validate_metric_namespace",
        "write_metrics_snapshot",
    ),
    "nats_sinks.file": (
        "FileCompression",
        "FileDuplicatePolicy",
        "FileFilenameStrategy",
        "FileSink",
        "FileSinkConfig",
        "FileWriteMode",
    ),
    "nats_sinks.oracle": (
        "OracleSink",
        "OracleSinkConfig",
    ),
    "nats_sinks.observability": (
        "NATS_MONITORING_ALLOWED_ENDPOINTS",
        "NATS_MONITORING_SNAPSHOT_SCHEMA",
        "OBSERVABILITY_POLICY_SCHEMA",
        "NatsMonitoringEndpointObservation",
        "NatsMonitoringError",
        "NatsServerMonitoringPolicy",
        "ObservabilityPolicy",
        "ObservabilitySubjectPolicy",
        "PrometheusHttpEndpointPolicy",
        "PrometheusHttpResponse",
        "PrometheusTextfilePolicy",
        "build_nats_monitoring_url",
        "build_prometheus_http_server",
        "build_policy_from_app_config",
        "collect_nats_monitoring_snapshot",
        "ensure_nats_monitoring_enabled",
        "ensure_prometheus_http_enabled",
        "extract_nats_monitoring_fields",
        "filter_metric_rows",
        "load_nats_monitoring_snapshot",
        "load_observability_policy",
        "observability_policy_template",
        "render_nats_monitoring_prometheus",
        "render_prometheus_http_response",
        "render_prometheus_textfile",
        "serve_prometheus_http",
        "write_nats_monitoring_snapshot",
        "write_observability_policy",
        "write_prometheus_textfile",
    ),
    "nats_sinks.sinks": (
        "FlushableSink",
        "HealthCheckableSink",
        "SchemaAwareSink",
        "Sink",
        "SinkFactory",
        "SinkRegistry",
    ),
}

DOCUMENTED_IMPORT_CONTRACT: dict[str, tuple[str, ...]] = {
    **PUBLIC_API_CONTRACT,
    "nats_sinks.cli.main": ("app",),
    "nats_sinks.cli.metrics": ("app",),
    "nats_sinks.cli.observability": ("app",),
    "nats_sinks.core.config": (
        "AppConfig",
        "CustodyConfig",
        "DeliveryConfig",
        "MissionMetadataConfig",
        "MissionMetadataRuleConfig",
        "NatsConfig",
        "PreSinkPolicyConfig",
        "PreSinkPolicyRuleConfig",
        "load_config",
        "redacted_config",
    ),
}

STABLE_CONSOLE_SCRIPTS: dict[str, str] = {
    "nats-sink": "nats_sinks.cli.main:app",
    "nats-sink-metrics": "nats_sinks.cli.metrics:app",
    "nats-sink-observe": "nats_sinks.cli.observability:app",
}


def _pyproject() -> dict[str, object]:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))


def _project_table() -> dict[str, Any]:
    return cast(dict[str, Any], _pyproject()["project"])


def test_documented_public_imports_remain_available() -> None:
    """Every documented module-level symbol in the contract can be imported."""

    for module_name, symbol_names in DOCUMENTED_IMPORT_CONTRACT.items():
        module = importlib.import_module(module_name)
        for symbol_name in symbol_names:
            assert getattr(module, symbol_name, None) is not None, f"{module_name}.{symbol_name}"


def test_public_exports_include_documented_contract() -> None:
    """`__all__` should include documented symbols for star-import users.

    Star imports are not the recommended style, but `__all__` is still useful
    for documentation tools, static analysis, and maintainers who want to see
    the intended public surface of a module.
    """

    for module_name, symbol_names in PUBLIC_API_CONTRACT.items():
        module = importlib.import_module(module_name)
        exported = set(getattr(module, "__all__", ()))
        assert set(symbol_names).issubset(exported), module_name


def test_public_api_smoke_imports_match_readme_examples() -> None:
    """The common README imports should keep working exactly as published."""

    assert ReadmeJetStreamSinkRunner is nats_sinks.JetStreamSinkRunner
    assert ReadmeNatsEnvelope is nats_sinks.NatsEnvelope
    assert ReadmeSink is nats_sinks.Sink
    assert ReadmeFileSink is nats_sinks.FileSink
    assert ReadmeOracleSink.__name__ == "OracleSink"


def test_public_metric_helper_contract_remains_stable() -> None:
    assert (
        nats_sinks.qualified_metric_name("messages_fetched_total")
        == "nats_sinks_messages_fetched_total"
    )


def test_console_script_entry_points_remain_stable() -> None:
    """PyPI users rely on the CLI command names published in metadata."""

    scripts = _project_table()["scripts"]

    assert scripts == STABLE_CONSOLE_SCRIPTS
    for entry_point in STABLE_CONSOLE_SCRIPTS.values():
        module_name, object_name = entry_point.split(":", maxsplit=1)
        command = getattr(importlib.import_module(module_name), object_name)
        assert isinstance(command, typer.Typer)


def test_runtime_version_matches_project_metadata() -> None:
    assert nats_sinks.__version__ == _project_table()["version"]
