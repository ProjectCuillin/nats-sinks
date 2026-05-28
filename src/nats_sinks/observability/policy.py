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
from dataclasses import dataclass
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from nats_sinks.core.config import AppConfig, load_json
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.metrics import METRIC_SPEC_BY_NAME, validate_metric_namespace
from nats_sinks.core.subjects import matches_subject, validate_subject_pattern

OBSERVABILITY_POLICY_SCHEMA = "nats_sinks.observability.policy.v1"
PROMETHEUS_HTTP_PATH_MAX_LENGTH = 128
OTLP_ENDPOINT_MAX_LENGTH = 512
OTLP_HEADER_NAME_MAX_LENGTH = 128
OTLP_HEADER_ENV_MAX_LENGTH = 128
NATS_MONITORING_ENDPOINT_MAX_LENGTH = 128
NATS_MONITORING_FIELD_MAX_LENGTH = 256
STATSD_MAX_DATAGRAM_BYTES = 65_507
STATSD_METRIC_PREFIX_MAX_LENGTH = 128
STATSD_SOCKET_PATH_MAX_LENGTH = 512
OCI_MONITORING_MAX_DIMENSIONS = 10
OCI_MONITORING_MAX_METADATA = 10
OCI_MONITORING_MAX_METRICS_PER_REQUEST = 50
OCI_MONITORING_MAX_REQUEST_BYTES = 1_048_576
OCI_MONITORING_NAMESPACE_MAX_LENGTH = 255
OCI_MONITORING_REGION_MAX_LENGTH = 64
OCI_MONITORING_COMPARTMENT_ID_MAX_LENGTH = 255
OCI_MONITORING_NAME_MAX_LENGTH = 255
OCI_MONITORING_DIMENSION_KEY_MAX_LENGTH = 256
OCI_MONITORING_DIMENSION_VALUE_MAX_LENGTH = 512
OCI_MONITORING_METADATA_VALUE_MAX_LENGTH = 256
OCI_MONITORING_RESOURCE_GROUP_MAX_LENGTH = 255
OCI_MONITORING_CONFIG_FILE_MAX_LENGTH = 512
OCI_MONITORING_PROFILE_MAX_LENGTH = 128
SYSLOG_MAX_MESSAGE_BYTES = 8_192
SYSLOG_HOSTNAME_MAX_LENGTH = 255
SYSLOG_APP_NAME_MAX_LENGTH = 48
SYSLOG_PROCID_MAX_LENGTH = 128
SYSLOG_MSGID_MAX_LENGTH = 32
SYSLOG_STRUCTURED_DATA_ID_MAX_LENGTH = 32
SYSLOG_SOCKET_PATH_MAX_LENGTH = 512
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
STATSD_METRIC_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
OCI_MONITORING_NAMESPACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,254}$")
OCI_MONITORING_REGION_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z]+)+-\d$")
OCI_MONITORING_COMPARTMENT_ID_RE = re.compile(r"^ocid1\.compartment\.[A-Za-z0-9_.-]+$")
OCI_MONITORING_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-$]{0,254}$")
OCI_MONITORING_DIMENSION_KEY_RE = re.compile(r"^[!-~]{1,256}$")
OCI_MONITORING_DIMENSION_VALUE_RE = re.compile(r"^[!-~]{1,512}$")
OCI_MONITORING_CONFIG_PROFILE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SYSLOG_PRINTABLE_RE = re.compile(r"^[!-~]+$")
SYSLOG_STRUCTURED_DATA_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
SUBJECT_FAMILY_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
SUBJECT_FAMILY_LABEL_SECRET_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "bearer",
        "cookie",
        "credential",
        "key",
        "password",
        "private",
        "secret_token",
        "token",
    }
)
OCI_MONITORING_DIMENSION_SECRET_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "bearer",
        "classification",
        "compartment",
        "credential",
        "file",
        "host",
        "key",
        "label",
        "message",
        "mission",
        "ocid",
        "password",
        "path",
        "private",
        "resource",
        "secret",
        "subject",
        "table",
        "tenancy",
        "token",
        "user",
    }
)
MAX_SUBJECT_AWARE_RULES = 128
MAX_SUBJECT_AWARE_FAMILIES = 100
SubjectAwareAction = Literal["allow", "deny"]
SubjectAwareDisplayMode = Literal["label", "redacted", "hash", "raw"]
SubjectAwareOverflowAction = Literal["drop", "aggregate_other", "fail_closed"]


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


def _validate_syslog_printable_field(value: str, *, field_name: str, max_length: int) -> str:
    """Validate one RFC 5424 header field or the nil marker."""

    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field_name} must not be empty")
    if len(rendered) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    if rendered == "-":
        return rendered
    if not SYSLOG_PRINTABLE_RE.fullmatch(rendered):
        raise ValueError(f"{field_name} must contain only printable ASCII without spaces")
    return rendered


def _validate_subject_family_label(value: str, *, field_name: str) -> str:
    """Validate a stable low-cardinality subject-family label."""

    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field_name} must not be empty")
    if not SUBJECT_FAMILY_LABEL_RE.fullmatch(rendered):
        raise ValueError(
            f"{field_name} must start with a letter or underscore and contain only "
            "letters, digits, underscores, dots, or hyphens"
        )
    lowered = rendered.lower()
    if any(part in lowered for part in SUBJECT_FAMILY_LABEL_SECRET_PARTS):
        raise ValueError(f"{field_name} must not look like a secret or credential")
    return rendered


def _validate_observability_metric_names(values: list[str], *, field_name: str) -> list[str]:
    """Validate exact metric names used by observability policy sections."""

    rendered: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            raise ValueError(f"{field_name} metric names must not be empty")
        if item not in METRIC_SPEC_BY_NAME:
            raise ValueError(f"unknown nats-sinks metric name: {item}")
        rendered.append(item)
    return rendered


def _validate_observability_metric_patterns(values: list[str], *, field_name: str) -> list[str]:
    """Validate bounded metric glob patterns used by observability policy sections."""

    rendered: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            raise ValueError(f"{field_name} metric patterns must not be empty")
        if "\x00" in item or "\n" in item or "\r" in item:
            raise ValueError(f"{field_name} metric patterns must not contain control characters")
        rendered.append(item)
    return rendered


@dataclass(frozen=True, slots=True)
class SubjectAwareDecision:
    """Result of evaluating a subject against the subject-aware policy model."""

    allowed: bool
    reason: str
    label: str | None = None
    display_mode: SubjectAwareDisplayMode | None = None


class SubjectAwareRule(BaseModel):
    """One explicit subject-family rule for future subject-aware metrics."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    action: SubjectAwareAction = "allow"
    label: str | None = None
    display_mode: SubjectAwareDisplayMode = "label"
    allowed_metrics: list[str] = Field(default_factory=list)
    allowed_metric_patterns: list[str] = Field(default_factory=list)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        """Validate subject-family rules with the shared NATS wildcard grammar."""

        return validate_subject_pattern(value)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str | None) -> str | None:
        """Validate optional operator-chosen family labels."""

        if value is None:
            return None
        return _validate_subject_family_label(value, field_name="subject_metrics.rules.label")

    @field_validator("allowed_metrics")
    @classmethod
    def validate_metric_names(cls, values: list[str]) -> list[str]:
        """Validate rule-scoped exact metric names."""

        return _validate_observability_metric_names(
            values,
            field_name="subject_metrics.rules.allowed_metrics",
        )

    @field_validator("allowed_metric_patterns")
    @classmethod
    def validate_metric_patterns(cls, values: list[str]) -> list[str]:
        """Validate rule-scoped metric glob patterns."""

        return _validate_observability_metric_patterns(
            values,
            field_name="subject_metrics.rules.allowed_metric_patterns",
        )

    @model_validator(mode="after")
    def validate_allow_rule_label(self) -> SubjectAwareRule:
        """Require reviewable stable labels for every allow rule."""

        if self.action == "allow" and self.label is None:
            raise ValueError("subject_metrics allow rules require a stable operator label")
        return self


class SubjectAwareObservabilityPolicy(BaseModel):
    """Disabled-by-default policy for controlled subject-family observability."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    default_action: Literal["deny"] = "deny"
    max_subject_families: int = Field(default=20, ge=1, le=MAX_SUBJECT_AWARE_FAMILIES)
    overflow_action: SubjectAwareOverflowAction = "drop"
    overflow_label: str = "other"
    allow_raw_subjects: bool = False
    rules: list[SubjectAwareRule] = Field(default_factory=list, max_length=MAX_SUBJECT_AWARE_RULES)

    @field_validator("overflow_label")
    @classmethod
    def validate_overflow_label(cls, value: str) -> str:
        """Validate the deterministic overflow bucket label."""

        return _validate_subject_family_label(
            value,
            field_name="subject_metrics.overflow_label",
        )

    @model_validator(mode="after")
    def validate_subject_policy(self) -> SubjectAwareObservabilityPolicy:
        """Fail closed for unsafe subject-aware policy shapes."""

        allow_rules = [rule for rule in self.rules if rule.action == "allow"]
        if len(allow_rules) > self.max_subject_families:
            raise ValueError("subject_metrics allow rules must not exceed max_subject_families")
        if any(rule.display_mode == "raw" for rule in allow_rules) and not self.allow_raw_subjects:
            raise ValueError("subject_metrics raw display mode requires allow_raw_subjects=true")
        return self


class ObservabilitySubjectPolicy(BaseModel):
    """Optional per-subject review hint for subject-aware policy planning.

    Current nats-sinks metrics are intentionally not labeled by subject because
    subject names can be sensitive and high-cardinality.  The policy still
    records known subject patterns as disabled hints so operators can review
    what the core config handles before writing explicit `subject_metrics`
    allow rules for future subject-aware connectors.
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


class StatsdObservabilityPolicy(BaseModel):
    """StatsD observability connector settings.

    The connector emits one datagram per policy-approved aggregate metric.  It
    is intentionally best-effort and observational: UDP and Unix datagram
    delivery can be lossy, and failures must never affect JetStream delivery or
    sink write behavior.  Metric names are generated from internal metric names
    and an optional static prefix only; subjects, payload values, classification
    values, labels, mission metadata, and destination details are not exported.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    transport: Literal["udp", "unixgram"] = "udp"
    host: str = "127.0.0.1"
    port: int = Field(default=8125, ge=1, le=65_535)
    socket_path: str | None = None
    metric_prefix: str | None = None
    timeout_seconds: float = Field(default=1.0, gt=0, le=60)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    stale_after_seconds: float | None = Field(default=None, gt=0, le=86_400)
    max_datagram_bytes: int = Field(default=1432, ge=128, le=STATSD_MAX_DATAGRAM_BYTES)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        """Validate the UDP target host without accepting path-like text."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("statsd.host must not be empty")
        if any(character in rendered for character in "\x00\n\r\t /"):
            raise ValueError(
                "statsd.host must not contain whitespace, slashes, or control characters"
            )
        return rendered

    @field_validator("socket_path")
    @classmethod
    def validate_socket_path(cls, value: str | None) -> str | None:
        """Validate the optional Unix datagram socket path."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("statsd.socket_path must not be empty")
        if len(rendered) > STATSD_SOCKET_PATH_MAX_LENGTH:
            raise ValueError(
                f"statsd.socket_path must be at most {STATSD_SOCKET_PATH_MAX_LENGTH} characters"
            )
        if any(character in rendered for character in "\x00\n\r"):
            raise ValueError("statsd.socket_path must not contain control characters")
        if Path(rendered).name in {"", ".", ".."}:
            raise ValueError("statsd.socket_path must name a socket path")
        return rendered

    @field_validator("metric_prefix")
    @classmethod
    def validate_metric_prefix(cls, value: str | None) -> str | None:
        """Validate an optional static StatsD metric prefix."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("statsd.metric_prefix must not be empty")
        if len(rendered) > STATSD_METRIC_PREFIX_MAX_LENGTH:
            raise ValueError(
                f"statsd.metric_prefix must be at most {STATSD_METRIC_PREFIX_MAX_LENGTH} characters"
            )
        if not STATSD_METRIC_PREFIX_RE.fullmatch(rendered):
            raise ValueError(
                "statsd.metric_prefix must start with a letter or underscore and contain only "
                "letters, digits, underscores, dots, and hyphens"
            )
        return rendered.strip(".")

    @model_validator(mode="after")
    def validate_transport_settings(self) -> StatsdObservabilityPolicy:
        """Require transport-specific settings only when StatsD is enabled."""

        if not self.enabled:
            return self
        if self.transport == "unixgram" and self.socket_path is None:
            raise ValueError("statsd.socket_path is required when statsd.transport is unixgram")
        return self


class OciMonitoringObservabilityPolicy(BaseModel):
    """Oracle Cloud Infrastructure Monitoring connector settings.

    The connector is disabled by default and belongs to the observability
    plane. It reads only local metrics snapshots, applies the shared allow-list
    policy, and sends bounded custom metric batches through the optional OCI
    Python SDK when explicitly enabled. OCI authentication is selected by
    runtime mode; policy files must never contain API keys, private keys,
    tenancy OCIDs, user OCIDs, fingerprints, passphrases, or session tokens.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    metric_namespace: str = "nats_sinks_metrics"
    region: str | None = None
    compartment_id: str | None = None
    resource_group: str | None = None
    auth_mode: Literal["instance_principal", "resource_principal", "config_file"] = (
        "instance_principal"
    )
    config_file: str | None = None
    profile: str = "DEFAULT"
    batch_atomicity: Literal["ATOMIC", "NON_ATOMIC"] = "ATOMIC"
    dimensions: dict[str, str] = Field(
        default_factory=lambda: {"source": "nats_sinks"},
        max_length=OCI_MONITORING_MAX_DIMENSIONS,
    )
    metadata: dict[str, str] = Field(default_factory=dict, max_length=OCI_MONITORING_MAX_METADATA)
    include_metric_labels_as_dimensions: bool = False
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    stale_after_seconds: float | None = Field(default=None, gt=0, le=86_400)
    max_metrics_per_request: int = Field(
        default=20,
        ge=1,
        le=OCI_MONITORING_MAX_METRICS_PER_REQUEST,
    )
    max_request_bytes: int = Field(
        default=OCI_MONITORING_MAX_REQUEST_BYTES,
        ge=1024,
        le=OCI_MONITORING_MAX_REQUEST_BYTES,
    )

    @field_validator("metric_namespace")
    @classmethod
    def validate_metric_namespace(cls, value: str) -> str:
        """Validate the OCI Monitoring custom metric namespace."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("oci_monitoring.metric_namespace must not be empty")
        if len(rendered) > OCI_MONITORING_NAMESPACE_MAX_LENGTH:
            raise ValueError(
                "oci_monitoring.metric_namespace must be at most "
                f"{OCI_MONITORING_NAMESPACE_MAX_LENGTH} characters"
            )
        lowered = rendered.lower()
        if lowered.startswith(("oci_", "oracle_")):
            raise ValueError(
                "oci_monitoring.metric_namespace must not start with reserved prefixes "
                "oci_ or oracle_"
            )
        if not OCI_MONITORING_NAMESPACE_RE.fullmatch(rendered):
            raise ValueError(
                "oci_monitoring.metric_namespace must start with a letter and contain "
                "only letters, digits, and underscores"
            )
        return rendered

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str | None) -> str | None:
        """Validate an optional OCI region without accepting URLs or secrets."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("oci_monitoring.region must not be empty")
        if len(rendered) > OCI_MONITORING_REGION_MAX_LENGTH:
            raise ValueError(
                "oci_monitoring.region must be at most "
                f"{OCI_MONITORING_REGION_MAX_LENGTH} characters"
            )
        if any(character in rendered for character in "\x00\n\r\t /:@"):
            raise ValueError(
                "oci_monitoring.region must be a plain OCI region name without whitespace, "
                "URLs, or credentials"
            )
        if not OCI_MONITORING_REGION_RE.fullmatch(rendered):
            raise ValueError(
                "oci_monitoring.region must look like an OCI region, for example eu-frankfurt-1"
            )
        return rendered

    @field_validator("compartment_id")
    @classmethod
    def validate_compartment_id(cls, value: str | None) -> str | None:
        """Validate the compartment OCID required for custom metrics."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("oci_monitoring.compartment_id must not be empty")
        if len(rendered) > OCI_MONITORING_COMPARTMENT_ID_MAX_LENGTH:
            raise ValueError("oci_monitoring.compartment_id is too long")
        if any(character in rendered for character in "\x00\n\r\t /:@"):
            raise ValueError(
                "oci_monitoring.compartment_id must be a plain compartment OCID without "
                "whitespace, URLs, or credentials"
            )
        if not OCI_MONITORING_COMPARTMENT_ID_RE.fullmatch(rendered):
            raise ValueError("oci_monitoring.compartment_id must look like a compartment OCID")
        return rendered

    @field_validator("resource_group")
    @classmethod
    def validate_resource_group(cls, value: str | None) -> str | None:
        """Validate an optional OCI Monitoring resource group."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("oci_monitoring.resource_group must not be empty")
        if len(rendered) > OCI_MONITORING_RESOURCE_GROUP_MAX_LENGTH:
            raise ValueError("oci_monitoring.resource_group is too long")
        if not OCI_MONITORING_NAME_RE.fullmatch(rendered):
            raise ValueError(
                "oci_monitoring.resource_group must start with a letter and contain only "
                "letters, digits, dots, underscores, hyphens, or dollar signs"
            )
        lowered = rendered.lower()
        if any(part in lowered for part in OCI_MONITORING_DIMENSION_SECRET_PARTS):
            raise ValueError("oci_monitoring.resource_group must not look sensitive")
        return rendered

    @field_validator("config_file")
    @classmethod
    def validate_config_file(cls, value: str | None) -> str | None:
        """Validate an optional OCI SDK config file path."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("oci_monitoring.config_file must not be empty")
        if len(rendered) > OCI_MONITORING_CONFIG_FILE_MAX_LENGTH:
            raise ValueError("oci_monitoring.config_file is too long")
        if any(character in rendered for character in "\x00\n\r"):
            raise ValueError("oci_monitoring.config_file must not contain control characters")
        if Path(rendered).name in {"", ".", ".."}:
            raise ValueError("oci_monitoring.config_file must name a file")
        return rendered

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        """Validate the optional OCI SDK config profile name."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("oci_monitoring.profile must not be empty")
        if len(rendered) > OCI_MONITORING_PROFILE_MAX_LENGTH:
            raise ValueError("oci_monitoring.profile is too long")
        if not OCI_MONITORING_CONFIG_PROFILE_RE.fullmatch(rendered):
            raise ValueError(
                "oci_monitoring.profile may contain only letters, digits, underscores, "
                "dots, colons, and hyphens"
            )
        return rendered

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate static OCI dimensions as low-cardinality hints."""

        if not value:
            raise ValueError("oci_monitoring.dimensions must include at least one safe dimension")
        rendered: dict[str, str] = {}
        seen_lower: set[str] = set()
        for raw_name, raw_value in value.items():
            name = raw_name.strip()
            dimension_value = raw_value.strip()
            if not name:
                raise ValueError("oci_monitoring.dimensions names must not be empty")
            if not dimension_value:
                raise ValueError("oci_monitoring.dimensions values must not be empty")
            if len(name) > OCI_MONITORING_DIMENSION_KEY_MAX_LENGTH:
                raise ValueError("oci_monitoring.dimensions names are too long")
            if len(dimension_value) > OCI_MONITORING_DIMENSION_VALUE_MAX_LENGTH:
                raise ValueError("oci_monitoring.dimensions values are too long")
            if " " in name or any(character in name for character in "\x00\n\r\t"):
                raise ValueError(
                    "oci_monitoring.dimensions names must not contain spaces or control characters"
                )
            if any(character in dimension_value for character in "\x00\n\r\t "):
                raise ValueError(
                    "oci_monitoring.dimensions values must not contain whitespace "
                    "or control characters"
                )
            if not OCI_MONITORING_DIMENSION_KEY_RE.fullmatch(name):
                raise ValueError("oci_monitoring.dimensions names must be printable ASCII")
            if not OCI_MONITORING_DIMENSION_VALUE_RE.fullmatch(dimension_value):
                raise ValueError("oci_monitoring.dimensions values must be printable ASCII")
            lowered = name.lower()
            if any(part in lowered for part in OCI_MONITORING_DIMENSION_SECRET_PARTS):
                raise ValueError(
                    "oci_monitoring.dimensions must not include sensitive or high-cardinality names"
                )
            if lowered in seen_lower:
                raise ValueError("oci_monitoring.dimensions names must be unique ignoring case")
            seen_lower.add(lowered)
            rendered[name] = dimension_value
        return rendered

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate optional OCI metric metadata as non-sensitive static hints."""

        rendered: dict[str, str] = {}
        seen_lower: set[str] = set()
        for raw_name, raw_value in value.items():
            name = raw_name.strip()
            metadata_value = raw_value.strip()
            if not name:
                raise ValueError("oci_monitoring.metadata names must not be empty")
            if not metadata_value:
                raise ValueError("oci_monitoring.metadata values must not be empty")
            if len(name) > OCI_MONITORING_DIMENSION_KEY_MAX_LENGTH:
                raise ValueError("oci_monitoring.metadata names are too long")
            if len(metadata_value) > OCI_MONITORING_METADATA_VALUE_MAX_LENGTH:
                raise ValueError("oci_monitoring.metadata values are too long")
            if any(character in name for character in "\x00\n\r\t "):
                raise ValueError("oci_monitoring.metadata names must not contain whitespace")
            if any(character in metadata_value for character in "\x00\n\r\t "):
                raise ValueError("oci_monitoring.metadata values must not contain whitespace")
            if not OCI_MONITORING_DIMENSION_KEY_RE.fullmatch(name):
                raise ValueError("oci_monitoring.metadata names must be printable ASCII")
            if not OCI_MONITORING_DIMENSION_VALUE_RE.fullmatch(metadata_value):
                raise ValueError("oci_monitoring.metadata values must be printable ASCII")
            lowered = name.lower()
            if any(part in lowered for part in OCI_MONITORING_DIMENSION_SECRET_PARTS):
                raise ValueError("oci_monitoring.metadata must not include sensitive names")
            if lowered in seen_lower:
                raise ValueError("oci_monitoring.metadata names must be unique ignoring case")
            seen_lower.add(lowered)
            rendered[name] = metadata_value
        return rendered

    @model_validator(mode="after")
    def validate_enabled_requirements(self) -> OciMonitoringObservabilityPolicy:
        """Require explicit OCI location and identity settings only when enabled."""

        if not self.enabled:
            return self
        if self.region is None:
            raise ValueError(
                "oci_monitoring.region is required when oci_monitoring.enabled is true"
            )
        if self.compartment_id is None:
            raise ValueError(
                "oci_monitoring.compartment_id is required when oci_monitoring.enabled is true"
            )
        if self.auth_mode == "config_file" and self.config_file is None:
            raise ValueError(
                "oci_monitoring.config_file is required when "
                "oci_monitoring.auth_mode is config_file"
            )
        if self.auth_mode != "config_file" and self.config_file is not None:
            raise ValueError(
                "oci_monitoring.config_file may be set only when auth_mode is config_file"
            )
        return self


class SyslogObservabilityPolicy(BaseModel):
    """Syslog observability bridge settings.

    The bridge emits one RFC 5424-style structured syslog message per
    policy-approved aggregate metric.  It is intentionally best-effort and
    observational: datagram transports can be lossy, receivers can drop data,
    and failures must never affect JetStream delivery or sink writes.  The
    message body is empty and all approved values live in bounded structured
    data parameters.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    transport: Literal["udp", "unixgram"] = "udp"
    host: str = "127.0.0.1"
    port: int = Field(default=514, ge=1, le=65_535)
    socket_path: str | None = None
    facility: Literal[
        "kern",
        "user",
        "mail",
        "daemon",
        "auth",
        "syslog",
        "lpr",
        "news",
        "uucp",
        "cron",
        "authpriv",
        "ftp",
        "ntp",
        "audit",
        "alert",
        "clock",
        "local0",
        "local1",
        "local2",
        "local3",
        "local4",
        "local5",
        "local6",
        "local7",
    ] = "local0"
    severity: Literal[
        "emergency",
        "alert",
        "critical",
        "error",
        "warning",
        "notice",
        "info",
        "debug",
    ] = "info"
    hostname: str = "-"
    app_name: str = "nats-sinks"
    procid: str = "-"
    msgid: str = "metrics"
    structured_data_id: str = "nats_sinks"
    timeout_seconds: float = Field(default=1.0, gt=0, le=60)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    stale_after_seconds: float | None = Field(default=None, gt=0, le=86_400)
    max_message_bytes: int = Field(default=1024, ge=128, le=SYSLOG_MAX_MESSAGE_BYTES)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        """Validate the UDP target host without accepting path-like text."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("syslog.host must not be empty")
        if any(character in rendered for character in "\x00\n\r\t /"):
            raise ValueError(
                "syslog.host must not contain whitespace, slashes, or control characters"
            )
        return rendered

    @field_validator("socket_path")
    @classmethod
    def validate_socket_path(cls, value: str | None) -> str | None:
        """Validate the optional Unix datagram socket path."""

        if value is None:
            return None
        rendered = value.strip()
        if not rendered:
            raise ValueError("syslog.socket_path must not be empty")
        if len(rendered) > SYSLOG_SOCKET_PATH_MAX_LENGTH:
            raise ValueError(
                f"syslog.socket_path must be at most {SYSLOG_SOCKET_PATH_MAX_LENGTH} characters"
            )
        if any(character in rendered for character in "\x00\n\r"):
            raise ValueError("syslog.socket_path must not contain control characters")
        if Path(rendered).name in {"", ".", ".."}:
            raise ValueError("syslog.socket_path must name a socket path")
        return rendered

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, value: str) -> str:
        """Validate the RFC 5424 hostname field without using local host lookup."""

        return _validate_syslog_printable_field(
            value,
            field_name="syslog.hostname",
            max_length=SYSLOG_HOSTNAME_MAX_LENGTH,
        )

    @field_validator("app_name")
    @classmethod
    def validate_app_name(cls, value: str) -> str:
        """Validate the RFC 5424 application name field."""

        return _validate_syslog_printable_field(
            value,
            field_name="syslog.app_name",
            max_length=SYSLOG_APP_NAME_MAX_LENGTH,
        )

    @field_validator("procid")
    @classmethod
    def validate_procid(cls, value: str) -> str:
        """Validate the RFC 5424 process identifier field."""

        return _validate_syslog_printable_field(
            value,
            field_name="syslog.procid",
            max_length=SYSLOG_PROCID_MAX_LENGTH,
        )

    @field_validator("msgid")
    @classmethod
    def validate_msgid(cls, value: str) -> str:
        """Validate the RFC 5424 message identifier field."""

        return _validate_syslog_printable_field(
            value,
            field_name="syslog.msgid",
            max_length=SYSLOG_MSGID_MAX_LENGTH,
        )

    @field_validator("structured_data_id")
    @classmethod
    def validate_structured_data_id(cls, value: str) -> str:
        """Validate the bounded structured-data identifier used for metrics."""

        rendered = value.strip()
        if not rendered:
            raise ValueError("syslog.structured_data_id must not be empty")
        if len(rendered) > SYSLOG_STRUCTURED_DATA_ID_MAX_LENGTH:
            raise ValueError(
                "syslog.structured_data_id must be at most "
                f"{SYSLOG_STRUCTURED_DATA_ID_MAX_LENGTH} characters"
            )
        if not SYSLOG_STRUCTURED_DATA_ID_RE.fullmatch(rendered):
            raise ValueError(
                "syslog.structured_data_id may contain only letters, digits, underscores, "
                "dots, colons, and hyphens"
            )
        return rendered

    @model_validator(mode="after")
    def validate_transport_settings(self) -> SyslogObservabilityPolicy:
        """Require transport-specific settings only when syslog is enabled."""

        if not self.enabled:
            return self
        if self.transport == "unixgram" and self.socket_path is None:
            raise ValueError("syslog.socket_path is required when syslog.transport is unixgram")
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
    subject_metrics: SubjectAwareObservabilityPolicy = Field(
        default_factory=SubjectAwareObservabilityPolicy
    )
    prometheus: PrometheusTextfilePolicy = Field(default_factory=PrometheusTextfilePolicy)
    otlp: OtlpMetricsPolicy = Field(default_factory=OtlpMetricsPolicy)
    elastic: ElasticObservabilityPolicy = Field(default_factory=ElasticObservabilityPolicy)
    grafana_alloy: GrafanaAlloyObservabilityPolicy = Field(
        default_factory=GrafanaAlloyObservabilityPolicy
    )
    splunk_hec: SplunkHecObservabilityPolicy = Field(default_factory=SplunkHecObservabilityPolicy)
    statsd: StatsdObservabilityPolicy = Field(default_factory=StatsdObservabilityPolicy)
    oci_monitoring: OciMonitoringObservabilityPolicy = Field(
        default_factory=OciMonitoringObservabilityPolicy
    )
    syslog: SyslogObservabilityPolicy = Field(default_factory=SyslogObservabilityPolicy)
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

        return _validate_observability_metric_names(values, field_name="observability policy")

    @field_validator("allowed_metric_patterns", "denied_metric_patterns")
    @classmethod
    def validate_metric_patterns(cls, values: list[str]) -> list[str]:
        """Reject empty or control-character glob patterns."""

        return _validate_observability_metric_patterns(values, field_name="observability policy")


def _base_metric_name(metric_name: str) -> str:
    """Return a base metric name accepted by exact policy checks."""

    if metric_name in METRIC_SPEC_BY_NAME:
        return metric_name
    base_name, separator, _stat = metric_name.rpartition(".")
    if separator and base_name in METRIC_SPEC_BY_NAME:
        return base_name
    return metric_name


def _subject_metric_matches(rule: SubjectAwareRule, metric_name: str | None) -> bool:
    """Return whether a rule applies to a metric name."""

    if metric_name is None:
        return True
    base_name = _base_metric_name(metric_name.strip())
    if base_name not in METRIC_SPEC_BY_NAME:
        return False
    if not rule.allowed_metrics and not rule.allowed_metric_patterns:
        return True
    return (
        metric_name in rule.allowed_metrics
        or base_name in rule.allowed_metrics
        or any(fnmatchcase(metric_name, pattern) for pattern in rule.allowed_metric_patterns)
        or any(fnmatchcase(base_name, pattern) for pattern in rule.allowed_metric_patterns)
    )


def _subject_family_label(
    *,
    rule: SubjectAwareRule,
    subject: str,
) -> str:
    """Render the future subject-family label according to a reviewed display mode."""

    if rule.display_mode == "label":
        return rule.label or "unknown"
    if rule.display_mode == "redacted":
        return "redacted"
    if rule.display_mode == "hash":
        digest = sha256(subject.encode("utf-8")).hexdigest()[:16]
        return f"sha256_{digest}"
    return subject


def evaluate_subject_observability_policy(
    policy: SubjectAwareObservabilityPolicy | ObservabilityPolicy,
    *,
    subject: str,
    metric_name: str | None = None,
) -> SubjectAwareDecision:
    """Evaluate a concrete subject against the fail-closed subject policy.

    This helper is intentionally independent from delivery code. It lets future
    observability connectors ask whether subject-family metadata may be shared
    without affecting ACK behavior, retries, DLQ handling, or sink writes.
    """

    subject_policy = policy.subject_metrics if isinstance(policy, ObservabilityPolicy) else policy
    if not subject_policy.enabled:
        return SubjectAwareDecision(allowed=False, reason="disabled")

    try:
        validated_subject = validate_subject_pattern(subject)
    except ConfigurationError:
        return SubjectAwareDecision(allowed=False, reason="invalid_subject")

    matching_deny = [
        rule
        for rule in subject_policy.rules
        if rule.action == "deny"
        and matches_subject(rule.subject, validated_subject)
        and _subject_metric_matches(rule, metric_name)
    ]
    if matching_deny:
        return SubjectAwareDecision(allowed=False, reason="denied")

    for rule in subject_policy.rules:
        if rule.action != "allow":
            continue
        if not matches_subject(rule.subject, validated_subject):
            continue
        if not _subject_metric_matches(rule, metric_name):
            continue
        return SubjectAwareDecision(
            allowed=True,
            reason="allowed",
            label=_subject_family_label(rule=rule, subject=validated_subject),
            display_mode=rule.display_mode,
        )

    return SubjectAwareDecision(allowed=False, reason="no_match")


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
