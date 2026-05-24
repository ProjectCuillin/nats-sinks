# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validated observability sharing policy.

Observability configuration is deliberately isolated from sink configuration.
The core runtime decides how messages are processed; this module decides which
already-recorded metrics may be shared with an external observability platform.

The safe default is no sharing.  Generated policies set `enabled` to false and
leave metric allow lists empty.  Operators must explicitly choose which metric
names or glob patterns are appropriate for their environment.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from nats_sinks.core.config import AppConfig, load_json
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import METRIC_SPEC_BY_NAME, validate_metric_namespace
from nats_sinks.core.subjects import validate_subject_pattern

OBSERVABILITY_POLICY_SCHEMA = "nats_sinks.observability.policy.v1"
PROMETHEUS_HTTP_PATH_MAX_LENGTH = 128
OTLP_ENDPOINT_MAX_LENGTH = 512
OTLP_HEADER_NAME_MAX_LENGTH = 128
OTLP_HEADER_ENV_MAX_LENGTH = 128
NATS_MONITORING_ENDPOINT_MAX_LENGTH = 128
NATS_MONITORING_FIELD_MAX_LENGTH = 256
NATS_MONITORING_ALLOWED_ENDPOINTS = {
    "/varz",
    "/connz",
    "/routez",
    "/subsz",
    "/accountz",
    "/accstatz",
    "/jsz",
    "/healthz",
}
HTTP_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
ENVIRONMENT_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ELASTIC_DATA_STREAM_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
GRAFANA_ALLOY_COMPONENT_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SPLUNK_HEC_INDEX_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$")
SPLUNK_HEC_METADATA_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")


def _validate_otlp_http_endpoint(value: str | None, *, field_name: str) -> str | None:
    """Validate an OTLP/HTTP endpoint without accepting embedded secrets."""

    if value is None:
        return None
    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field_name} must not be empty")
    if len(rendered) > OTLP_ENDPOINT_MAX_LENGTH:
        raise ValueError(f"{field_name} must be at most {OTLP_ENDPOINT_MAX_LENGTH} characters")
    if any(character in rendered for character in "\x00\n\r\t "):
        raise ValueError(f"{field_name} must not contain whitespace or control characters")

    parsed = urlsplit(rendered)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must use http or https")
    if not parsed.netloc:
        raise ValueError(f"{field_name} must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not include query strings or fragments")
    if not parsed.path.startswith("/"):
        raise ValueError(f"{field_name} must include an HTTP path")

    host = parsed.hostname or ""
    if parsed.scheme == "http" and host.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"{field_name} may use plain http only for loopback collectors")
    return rendered


def _validate_http_headers_env(value: dict[str, str], *, field_name: str) -> dict[str, str]:
    """Validate HTTP header names and environment-variable sources."""

    rendered: dict[str, str] = {}
    seen_lower: set[str] = set()
    for header_name, env_name in value.items():
        header = header_name.strip()
        source = env_name.strip()
        if not header:
            raise ValueError(f"{field_name} header names must not be empty")
        if len(header) > OTLP_HEADER_NAME_MAX_LENGTH:
            raise ValueError(f"{field_name} header names are too long")
        if not HTTP_HEADER_NAME_RE.fullmatch(header):
            raise ValueError(f"{field_name} header names are not valid HTTP field names")
        lowered = header.lower()
        if lowered in seen_lower:
            raise ValueError(f"{field_name} header names must be unique ignoring case")
        seen_lower.add(lowered)

        if not source:
            raise ValueError(f"{field_name} values must not be empty")
        if len(source) > OTLP_HEADER_ENV_MAX_LENGTH:
            raise ValueError(f"{field_name} environment variable names are too long")
        if not ENVIRONMENT_VARIABLE_NAME_RE.fullmatch(source):
            raise ValueError(f"{field_name} values must be environment variable names")
        rendered[header] = source
    return rendered


def _validate_elastic_data_stream_component(value: str, *, field_name: str) -> str:
    """Validate Elastic data stream routing components as bounded names."""

    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field_name} must not be empty")
    if not ELASTIC_DATA_STREAM_COMPONENT_RE.fullmatch(rendered):
        raise ValueError(
            f"{field_name} may contain only letters, digits, underscores, dots, and hyphens, "
            "and must start with a letter, digit, or underscore"
        )
    if rendered in {".", ".."}:
        raise ValueError(f"{field_name} must not be a relative path marker")
    return rendered


class ObservabilitySubjectPolicy(BaseModel):
    """Optional per-subject sharing hint for future subject-aware metrics.

    Current nats-sinks metrics are intentionally not labeled by subject because
    subject names can be sensitive and high-cardinality.  The policy still
    records known subject patterns as disabled hints so operators can review
    what the core config handles before enabling future subject-aware metrics.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    enabled: bool = False
    allowed_metrics: list[str] = Field(default_factory=list)
    allowed_metric_patterns: list[str] = Field(default_factory=list)
    share_subject_label: bool = False

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        """Validate subject patterns with the same NATS wildcard rules."""

        return validate_subject_pattern(value)


class PrometheusHttpEndpointPolicy(BaseModel):
    """Native Prometheus scrape endpoint settings.

    The endpoint is intentionally disabled by default and binds to loopback by
    default.  It is a separate observability connector: it reads approved local
    metrics snapshots and serves policy-filtered Prometheus text without
    connecting to NATS, Oracle, file sinks, or future destinations.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=9108, ge=1, le=65_535)
    path: str = "/metrics"
    request_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    response_max_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        """Validate the listener host without guessing operator intent."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("prometheus.http_endpoint.host must not be empty")
        if any(character in rendered for character in "\x00\n\r\t /"):
            raise ValueError(
                "prometheus.http_endpoint.host must not contain whitespace, slashes, "
                "or control characters"
            )
        return rendered

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Validate the scrape path as a small explicit HTTP path."""

        rendered = value.strip()
        if not rendered.startswith("/"):
            raise ValueError("prometheus.http_endpoint.path must start with '/'")
        if len(rendered) > PROMETHEUS_HTTP_PATH_MAX_LENGTH:
            raise ValueError(
                "prometheus.http_endpoint.path must be at most "
                f"{PROMETHEUS_HTTP_PATH_MAX_LENGTH} characters"
            )
        if any(character in rendered for character in "\x00\n\r\t ?#"):
            raise ValueError(
                "prometheus.http_endpoint.path must not contain whitespace, query strings, "
                "fragments, or control characters"
            )
        if rendered in {"", "."}:
            raise ValueError("prometheus.http_endpoint.path must be a real path")
        return rendered


class PrometheusTextfilePolicy(BaseModel):
    """Prometheus connector settings.

    The connector writes Prometheus exposition text that node_exporter can read
    through its textfile collector.  It does not open a network port and does
    not connect to NATS, Oracle, file sinks, or any future destination backend.
    The nested `http_endpoint` settings are for the optional native scrape
    endpoint, which uses the same allow-list policy and is also disabled by
    default.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    output_file: str | None = None
    include_help: bool = True
    include_type: bool = True
    stale_after_seconds: float | None = Field(default=None, gt=0, le=86_400)
    http_endpoint: PrometheusHttpEndpointPolicy = Field(
        default_factory=PrometheusHttpEndpointPolicy
    )

    @field_validator("output_file")
    @classmethod
    def validate_output_file(cls, value: str | None) -> str | None:
        """Reject ambiguous textfile output paths."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("prometheus.output_file must not be empty")
        if "\x00" in rendered or "\n" in rendered or "\r" in rendered:
            raise ValueError("prometheus.output_file must not contain control characters")
        if Path(rendered).name in {"", ".", ".."}:
            raise ValueError("prometheus.output_file must name a file")
        return rendered


class OtlpMetricsPolicy(BaseModel):
    """OpenTelemetry OTLP metrics connector settings.

    The connector is disabled by default and belongs to the observability plane,
    not the message delivery plane.  It reads only a local nats-sinks metrics
    snapshot, applies the shared observability allow/deny policy, and sends a
    bounded OTLP/HTTP JSON request to an explicitly configured collector
    endpoint.  Header values are loaded from environment variables so bearer
    tokens and other collector credentials are never stored in policy JSON.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    endpoint: str | None = None
    protocol: Literal["http_json"] = "http_json"
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    stale_after_seconds: float | None = Field(default=None, gt=0, le=86_400)
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    headers_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        """Validate an OTLP/HTTP endpoint without accepting embedded secrets."""

        return _validate_otlp_http_endpoint(value, field_name="otlp.endpoint")

    @field_validator("headers_env")
    @classmethod
    def validate_headers_env(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate collector header names and their environment-variable sources."""

        return _validate_http_headers_env(value, field_name="otlp.headers_env")

    @model_validator(mode="after")
    def validate_enabled_endpoint(self) -> OtlpMetricsPolicy:
        """Require an explicit endpoint only when OTLP export is enabled."""

        if self.enabled and self.endpoint is None:
            raise ValueError("otlp.endpoint is required when otlp.enabled is true")
        return self


class ElasticObservabilityPolicy(BaseModel):
    """Elastic Observability profile over the shared OTLP connector.

    The first Elastic implementation intentionally sends policy-approved
    metrics to an explicitly configured OTLP Collector or Elastic-managed OTLP
    endpoint shape.  It does not write directly to Elasticsearch indices, does
    not use the Bulk API, and does not add labels derived from subjects,
    payloads, message IDs, file paths, table names, or mission metadata values.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    ingestion_path: Literal["otlp_collector"] = "otlp_collector"
    endpoint: str | None = None
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    stale_after_seconds: float | None = Field(default=None, gt=0, le=86_400)
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    headers_env: dict[str, str] = Field(default_factory=dict)
    data_stream_dataset: str = "nats_sinks.metrics"
    data_stream_namespace: str = "default"

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        """Validate the Elastic profile OTLP endpoint."""

        return _validate_otlp_http_endpoint(value, field_name="elastic.endpoint")

    @field_validator("headers_env")
    @classmethod
    def validate_headers_env(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate Elastic profile header names and env-var sources."""

        return _validate_http_headers_env(value, field_name="elastic.headers_env")

    @field_validator("data_stream_dataset")
    @classmethod
    def validate_data_stream_dataset(cls, value: str) -> str:
        """Validate the low-cardinality Elastic data stream dataset hint."""

        return _validate_elastic_data_stream_component(
            value,
            field_name="elastic.data_stream_dataset",
        )

    @field_validator("data_stream_namespace")
    @classmethod
    def validate_data_stream_namespace(cls, value: str) -> str:
        """Validate the low-cardinality Elastic data stream namespace hint."""

        return _validate_elastic_data_stream_component(
            value,
            field_name="elastic.data_stream_namespace",
        )

    @model_validator(mode="after")
    def validate_enabled_endpoint(self) -> ElasticObservabilityPolicy:
        """Require an explicit endpoint only when Elastic export is enabled."""

        if self.enabled and self.endpoint is None:
            raise ValueError("elastic.endpoint is required when elastic.enabled is true")
        return self


class GrafanaAlloyObservabilityPolicy(BaseModel):
    """Grafana Alloy profile over the shared OTLP connector.

    The profile sends policy-approved metrics to a local or nearby Alloy
    `otelcol.receiver.otlp` HTTP endpoint.  Alloy is then responsible for
    batching, queueing, authentication, and forwarding into Grafana Cloud,
    Mimir, or another OTLP-compatible destination.  The nats-sinks side remains
    intentionally small and delivery-independent.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    handoff_mode: Literal["otlp_http"] = "otlp_http"
    endpoint: str | None = None
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    stale_after_seconds: float | None = Field(default=None, gt=0, le=86_400)
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    headers_env: dict[str, str] = Field(default_factory=dict)
    receiver_label: str = "nats_sinks"
    batch_label: str = "nats_sinks_batch"
    exporter_label: str = "grafana_cloud"
    auth_label: str = "grafana_cloud_auth"
    upstream_endpoint_env: str = "GRAFANA_CLOUD_OTLP_ENDPOINT"
    upstream_auth_mode: Literal["none", "basic"] = "none"
    upstream_auth_username_env: str | None = None
    upstream_auth_password_env: str | None = None

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        """Validate the local Alloy OTLP/HTTP receiver endpoint."""

        rendered = _validate_otlp_http_endpoint(value, field_name="grafana_alloy.endpoint")
        if rendered is None:
            return None
        parsed = urlsplit(rendered)
        if parsed.path != "/v1/metrics":
            raise ValueError("grafana_alloy.endpoint must use the OTLP metrics path /v1/metrics")
        return rendered

    @field_validator("headers_env")
    @classmethod
    def validate_headers_env(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate optional local Alloy receiver header env-var sources."""

        return _validate_http_headers_env(value, field_name="grafana_alloy.headers_env")

    @field_validator("receiver_label", "batch_label", "exporter_label", "auth_label")
    @classmethod
    def validate_component_label(cls, value: str) -> str:
        """Validate Alloy component labels as small explicit identifiers."""

        rendered = value.strip()
        if not GRAFANA_ALLOY_COMPONENT_LABEL_RE.fullmatch(rendered):
            raise ValueError(
                "grafana_alloy component labels must start with a letter or underscore "
                "and contain only letters, digits, and underscores"
            )
        return rendered

    @field_validator(
        "upstream_endpoint_env", "upstream_auth_username_env", "upstream_auth_password_env"
    )
    @classmethod
    def validate_upstream_env(cls, value: str | None) -> str | None:
        """Validate upstream Alloy secret references as environment names only."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("grafana_alloy upstream environment names must not be empty")
        if len(rendered) > OTLP_HEADER_ENV_MAX_LENGTH:
            raise ValueError("grafana_alloy upstream environment names are too long")
        if not ENVIRONMENT_VARIABLE_NAME_RE.fullmatch(rendered):
            raise ValueError("grafana_alloy upstream references must be environment variable names")
        return rendered

    @model_validator(mode="after")
    def validate_enabled_endpoint_and_auth(self) -> GrafanaAlloyObservabilityPolicy:
        """Require explicit local handoff and complete upstream auth settings."""

        if self.enabled and self.endpoint is None:
            raise ValueError(
                "grafana_alloy.endpoint is required when grafana_alloy.enabled is true"
            )
        if self.upstream_auth_mode == "basic":
            if self.upstream_auth_username_env is None or self.upstream_auth_password_env is None:
                raise ValueError(
                    "grafana_alloy basic upstream auth requires "
                    "upstream_auth_username_env and upstream_auth_password_env"
                )
        return self


class SplunkHecObservabilityPolicy(BaseModel):
    """Splunk HTTP Event Collector observability connector settings.

    The connector sends one bounded HEC metric event containing only
    policy-approved aggregate metrics.  It never sends message payloads,
    subjects, classification labels, mission metadata, message IDs, file paths,
    table names, or endpoint details.  HEC tokens are referenced by environment
    variable name so secrets stay outside policy files and CLI arguments.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    endpoint: str | None = None
    token_env: str | None = None
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    stale_after_seconds: float | None = Field(default=None, gt=0, le=86_400)
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    verify_tls: Literal[True] = True
    headers_env: dict[str, str] = Field(default_factory=dict)
    source: str = "nats-sinks"
    sourcetype: str = "nats_sinks:metrics"
    host: str = "nats-sinks"
    index: str | None = None

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        """Validate the Splunk HEC event endpoint without embedded secrets."""

        rendered = _validate_otlp_http_endpoint(value, field_name="splunk_hec.endpoint")
        if rendered is None:
            return None
        parsed = urlsplit(rendered)
        if parsed.path != "/services/collector/event":
            raise ValueError(
                "splunk_hec.endpoint must use the HEC JSON event path /services/collector/event"
            )
        return rendered

    @field_validator("token_env")
    @classmethod
    def validate_token_env(cls, value: str | None) -> str | None:
        """Validate the HEC token reference as an environment variable name."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("splunk_hec.token_env must not be empty")
        if len(rendered) > OTLP_HEADER_ENV_MAX_LENGTH:
            raise ValueError("splunk_hec.token_env is too long")
        if not ENVIRONMENT_VARIABLE_NAME_RE.fullmatch(rendered):
            raise ValueError("splunk_hec.token_env must be an environment variable name")
        return rendered

    @field_validator("headers_env")
    @classmethod
    def validate_headers_env(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate optional additional HEC header env-var sources."""

        rendered = _validate_http_headers_env(value, field_name="splunk_hec.headers_env")
        if any(header.lower() == "authorization" for header in rendered):
            raise ValueError("splunk_hec.headers_env must not override Authorization")
        if any(header.lower() == "content-type" for header in rendered):
            raise ValueError("splunk_hec.headers_env must not override Content-Type")
        return rendered

    @field_validator("source", "sourcetype", "host")
    @classmethod
    def validate_metadata_component(cls, value: str) -> str:
        """Validate small low-cardinality HEC metadata components."""

        rendered = value.strip()
        if not SPLUNK_HEC_METADATA_RE.fullmatch(rendered):
            raise ValueError(
                "splunk_hec metadata values must start with a letter, digit, or underscore "
                "and contain only letters, digits, underscores, dots, colons, and hyphens"
            )
        return rendered

    @field_validator("index")
    @classmethod
    def validate_index(cls, value: str | None) -> str | None:
        """Validate optional Splunk index routing as a small explicit name."""

        if value is None:
            return None
        rendered = value.strip()
        if not SPLUNK_HEC_INDEX_RE.fullmatch(rendered):
            raise ValueError(
                "splunk_hec.index must start with a letter, digit, or underscore "
                "and contain only letters, digits, underscores, and hyphens"
            )
        return rendered

    @model_validator(mode="after")
    def validate_enabled_endpoint_and_token(self) -> SplunkHecObservabilityPolicy:
        """Require explicit endpoint and token reference when HEC export is enabled."""

        if self.enabled and self.endpoint is None:
            raise ValueError("splunk_hec.endpoint is required when splunk_hec.enabled is true")
        if self.enabled and self.token_env is None:
            raise ValueError("splunk_hec.token_env is required when splunk_hec.enabled is true")
        return self


class NatsServerMonitoringPolicy(BaseModel):
    """Policy for the optional NATS server monitoring connector.

    The connector is observational only.  It polls selected NATS monitoring
    endpoints such as `/healthz` or `/jsz` from a separate `nats-sink-observe`
    command and must never be used by the delivery runner to decide ACK, NAK,
    retry, DLQ, or sink-write behavior.  Monitoring endpoints can expose
    operational posture, topology, and traffic tempo, so every sharing surface is
    disabled by default and must be explicitly enabled by an operator.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_url: str | None = None
    allowed_endpoints: list[str] = Field(default_factory=list)
    allowed_fields: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    max_response_bytes: int = Field(default=262_144, ge=1024, le=5_242_880)
    verify_tls: bool = True
    ca_file: str | None = None
    prometheus_enabled: bool = False
    include_help: bool = True
    include_type: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        """Validate a NATS monitoring base URL without accepting credentials."""

        if value is None:
            return None
        rendered = value.strip().rstrip("/")
        if not rendered:
            raise ValueError("nats_server_monitoring.base_url must not be empty")
        if any(character in rendered for character in "\x00\n\r\t "):
            raise ValueError("nats_server_monitoring.base_url must not contain whitespace")

        parsed = urlsplit(rendered)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("nats_server_monitoring.base_url must use http or https")
        if not parsed.netloc:
            raise ValueError("nats_server_monitoring.base_url must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("nats_server_monitoring.base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("nats_server_monitoring.base_url must not include query or fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("nats_server_monitoring.base_url must not include an endpoint path")

        host = parsed.hostname or ""
        if parsed.scheme == "http" and host.lower() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError(
                "nats_server_monitoring.base_url may use plain http only for loopback hosts"
            )
        return rendered

    @field_validator("allowed_endpoints")
    @classmethod
    def validate_allowed_endpoints(cls, values: list[str]) -> list[str]:
        """Allow only known NATS monitoring endpoint paths."""

        rendered: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("nats_server_monitoring.allowed_endpoints must not be empty")
            if len(item) > NATS_MONITORING_ENDPOINT_MAX_LENGTH:
                raise ValueError("nats_server_monitoring.allowed_endpoints entries are too long")
            if any(character in item for character in "\x00\n\r\t ?#"):
                raise ValueError(
                    "nats_server_monitoring.allowed_endpoints must not contain whitespace, "
                    "query strings, fragments, or control characters"
                )
            if item not in NATS_MONITORING_ALLOWED_ENDPOINTS:
                raise ValueError(f"unsupported NATS monitoring endpoint: {item}")
            if item not in seen:
                seen.add(item)
                rendered.append(item)
        return rendered

    @field_validator("allowed_fields")
    @classmethod
    def validate_allowed_fields(cls, values: list[str]) -> list[str]:
        """Validate dotted JSON field paths used for extraction and export."""

        rendered: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("nats_server_monitoring.allowed_fields must not be empty")
            if len(item) > NATS_MONITORING_FIELD_MAX_LENGTH:
                raise ValueError("nats_server_monitoring.allowed_fields entries are too long")
            if any(character in item for character in "\x00\n\r\t *?[]{}()"):
                raise ValueError(
                    "nats_server_monitoring.allowed_fields entries must be explicit dotted "
                    "JSON field paths"
                )
            parts = item.split(".")
            if any(not part for part in parts):
                raise ValueError(
                    "nats_server_monitoring.allowed_fields entries must not contain empty "
                    "path segments"
                )
            for part in parts:
                if not all(character.isalnum() or character in {"_", "-"} for character in part):
                    raise ValueError(
                        "nats_server_monitoring.allowed_fields may contain only letters, "
                        "digits, underscores, hyphens, and dots"
                    )
            if item not in seen:
                seen.add(item)
                rendered.append(item)
        return rendered

    @field_validator("ca_file")
    @classmethod
    def validate_ca_file(cls, value: str | None) -> str | None:
        """Reject ambiguous CA certificate paths without reading the file."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("nats_server_monitoring.ca_file must not be empty")
        if "\x00" in rendered or "\n" in rendered or "\r" in rendered:
            raise ValueError("nats_server_monitoring.ca_file must not contain control characters")
        if Path(rendered).name in {"", ".", ".."}:
            raise ValueError("nats_server_monitoring.ca_file must name a file")
        return rendered


class ObservabilityPolicy(BaseModel):
    """Top-level policy for sharing local metrics with observability tools."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["nats_sinks.observability.policy.v1"] = Field(
        default="nats_sinks.observability.policy.v1",
        alias="schema",
    )
    enabled: bool = False
    namespace: str = "nats_sinks"
    allowed_metrics: list[str] = Field(default_factory=list)
    allowed_metric_patterns: list[str] = Field(default_factory=list)
    denied_metrics: list[str] = Field(default_factory=list)
    denied_metric_patterns: list[str] = Field(default_factory=list)
    include_observations: bool = False
    include_legacy: bool = False
    subjects: list[ObservabilitySubjectPolicy] = Field(default_factory=list)
    prometheus: PrometheusTextfilePolicy = Field(default_factory=PrometheusTextfilePolicy)
    otlp: OtlpMetricsPolicy = Field(default_factory=OtlpMetricsPolicy)
    elastic: ElasticObservabilityPolicy = Field(default_factory=ElasticObservabilityPolicy)
    grafana_alloy: GrafanaAlloyObservabilityPolicy = Field(
        default_factory=GrafanaAlloyObservabilityPolicy
    )
    splunk_hec: SplunkHecObservabilityPolicy = Field(default_factory=SplunkHecObservabilityPolicy)
    nats_server_monitoring: NatsServerMonitoringPolicy = Field(
        default_factory=NatsServerMonitoringPolicy
    )

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        """Require a Prometheus-safe metric namespace."""

        return validate_metric_namespace(value)

    @field_validator("allowed_metrics", "denied_metrics")
    @classmethod
    def validate_metric_names(cls, values: list[str]) -> list[str]:
        """Allow only known nats-sinks metric names in exact-name lists."""

        rendered: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("metric names must not be empty")
            if item not in METRIC_SPEC_BY_NAME:
                raise ValueError(f"unknown nats-sinks metric name: {item}")
            rendered.append(item)
        return rendered

    @field_validator("allowed_metric_patterns", "denied_metric_patterns")
    @classmethod
    def validate_metric_patterns(cls, values: list[str]) -> list[str]:
        """Reject empty or control-character glob patterns."""

        rendered: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("metric patterns must not be empty")
            if "\x00" in item or "\n" in item or "\r" in item:
                raise ValueError("metric patterns must not contain control characters")
            rendered.append(item)
        return rendered


def _unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _raw_sink_subjects(config: AppConfig) -> list[str]:
    raw_sink = config.sink.model_dump(mode="python")
    subjects: list[str] = []
    table_routes = raw_sink.get("table_routes")
    if isinstance(table_routes, list):
        for route in table_routes:
            if isinstance(route, dict) and isinstance(route.get("subject"), str):
                subjects.append(route["subject"])
    return subjects


def subjects_from_app_config(config: AppConfig) -> list[str]:
    """Return subject patterns visible in validated core configuration."""

    subjects = [config.nats.subject]
    subjects.extend(rule.subject for rule in config.encryption.rules)
    subjects.extend(rule.subject for rule in config.message_metadata.rules)
    subjects.extend(rule.subject for rule in config.mission_metadata.rules)
    subjects.extend(rule.subject for rule in config.security_labels.rules)
    subjects.extend(_raw_sink_subjects(config))
    return _unique_preserve_order(validate_subject_pattern(subject) for subject in subjects)


def build_policy_from_app_config(
    config: AppConfig,
    *,
    output_file: str | None = None,
) -> ObservabilityPolicy:
    """Build a disabled sharing policy from a core nats-sinks configuration."""

    return ObservabilityPolicy(
        enabled=False,
        namespace=config.metrics.namespace,
        allowed_metrics=[],
        allowed_metric_patterns=[],
        subjects=[
            ObservabilitySubjectPolicy(subject=subject, enabled=False)
            for subject in subjects_from_app_config(config)
        ],
        prometheus=PrometheusTextfilePolicy(
            enabled=False,
            output_file=output_file,
        ),
    )


def observability_policy_template(
    config: AppConfig,
    *,
    output_file: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable disabled observability policy template."""

    policy = build_policy_from_app_config(config, output_file=output_file)
    return policy.model_dump(mode="json", exclude_none=True)


def load_observability_policy(path: str | Path) -> ObservabilityPolicy:
    """Load and validate an observability policy JSON file."""

    file_path = Path(path)
    try:
        raw = load_json(file_path)
    except ConfigurationError:
        raise
    try:
        return ObservabilityPolicy.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(str(exc)) from exc


def write_observability_policy(
    policy: ObservabilityPolicy | dict[str, Any],
    path: str | Path,
    *,
    overwrite: bool = False,
) -> None:
    """Write an observability policy atomically with restrictive permissions."""

    destination = Path(path)
    if destination.exists() and not overwrite:
        raise ConfigurationError(f"observability policy already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(policy, ObservabilityPolicy):
        payload = policy.model_dump(mode="json", exclude_none=True)
    else:
        payload = policy
    try:
        rendered = json.dumps(payload, indent=2, sort_keys=False, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "observability policy contains non-finite or non-serializable JSON values"
        ) from exc
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o640)
        os.replace(temp_name, destination)
    finally:
        if temp_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)
