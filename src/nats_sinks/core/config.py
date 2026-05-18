# SPDX-License-Identifier: Apache-2.0
"""JSON configuration models and safe loading helpers.

The configuration module is the single entry point for converting user-provided
configuration files into validated runtime objects.  Runtime configuration is
intentionally JSON-only: JSON has a small parsing surface, maps directly to the
Pydantic model tree, and avoids parser features that can surprise operators
during deployment.

The loader performs three tasks in a strict order.  First, it reads a UTF-8 JSON
document and verifies that the root value is an object.  Second, it applies a
small allow-list of environment variable overrides for values commonly injected
by deployment platforms.  Third, it validates the final structure with Pydantic
models using `extra="forbid"` on the core sections so misspelled keys fail fast.

This module also owns redaction for CLI output and logs.  It never resolves sink
passwords while rendering an effective configuration and it treats fields with
names such as password, token, secret, credentials, and private_key as sensitive.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.errors import ConfigurationError

SENSITIVE_KEY_PARTS = ("password", "token", "secret", "private_key", "credentials", "creds")


class NatsConfig(BaseModel):
    """NATS and JetStream connection configuration.

    Authentication fields map directly to supported `nats-py` connection
    options.  The preferred production shape keeps secret values out of the JSON
    file by using `password_env` or `token_env`; the process resolves those
    variables only when constructing a live NATS connection.  Redacted
    configuration output therefore shows the variable names without printing
    resolved secret values.

    Bcrypted passwords and tokens are a NATS server-side storage detail.  A
    client still authenticates with the clear-text value, ideally read from a
    secret manager or environment variable and always protected in transit with
    TLS.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = "nats://localhost:4222"
    stream: str
    consumer: str
    subject: str
    durable: bool = True
    name: str | None = None
    user: str | None = None
    password: str | None = None
    password_env: str | None = None
    token: str | None = None
    token_env: str | None = None
    creds_file: str | None = None
    nkey_seed_file: str | None = None
    tls_ca_file: str | None = None
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    tls_verify: bool = True

    @model_validator(mode="after")
    def validate_secret_sources(self) -> NatsConfig:
        """Reject ambiguous secret sources and incomplete TLS key settings."""

        if self.password is not None and self.password_env is not None:
            raise ValueError("configure either nats.password or nats.password_env, not both")
        if self.token is not None and self.token_env is not None:
            raise ValueError("configure either nats.token or nats.token_env, not both")
        if self.tls_key_file is not None and self.tls_cert_file is None:
            raise ValueError("nats.tls_key_file requires nats.tls_cert_file")
        return self

    def resolve_password(self) -> str | None:
        """Resolve the NATS password only when opening a connection."""

        if self.password is not None:
            return self.password
        if self.password_env is None:
            return None
        password = os.getenv(self.password_env)
        if password is None:
            raise ConfigurationError(f"environment variable {self.password_env} is not set")
        return password

    def resolve_token(self) -> str | None:
        """Resolve the NATS token only when opening a connection."""

        if self.token is not None:
            return self.token
        if self.token_env is None:
            return None
        token = os.getenv(self.token_env)
        if token is None:
            raise ConfigurationError(f"environment variable {self.token_env} is not set")
        return token


class DeliveryConfig(BaseModel):
    """Delivery behavior. ACK policy is intentionally fixed to commit-then-ack."""

    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=100, ge=1, le=10_000)
    batch_timeout_ms: int = Field(default=1000, ge=1)
    max_in_flight_batches: int = Field(default=1, ge=1, le=64)
    ack_policy: Literal["after_sink_commit"] = "after_sink_commit"
    max_retries: int = Field(default=5, ge=0)
    retry_backoff_ms: int = Field(default=1000, ge=0)
    temporary_failure_action: Literal["nak", "leave_unacked"] = "nak"
    prefer_safe_duplication: bool = True


class DeadLetterConfig(BaseModel):
    """Dead-letter queue publication settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    subject: str | None = None
    include_payload: bool = True
    include_headers: bool = True
    include_error: bool = True


class LoggingConfig(BaseModel):
    """Logging settings."""

    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    payload_logging: bool = False


class MetricsConfig(BaseModel):
    """Metrics settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    namespace: str = "nats_sinks"


class SinkConfig(BaseModel):
    """Raw sink configuration selected through the safe registry."""

    model_config = ConfigDict(extra="allow")

    type: str


class AppConfig(BaseModel):
    """Top-level application configuration."""

    model_config = ConfigDict(extra="forbid")

    nats: NatsConfig
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    dead_letter: DeadLetterConfig = Field(default_factory=DeadLetterConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    sink: SinkConfig


ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "NATS_SINKS_NATS_URL": ("nats", "url"),
    "NATS_SINKS_NATS_STREAM": ("nats", "stream"),
    "NATS_SINKS_NATS_CONSUMER": ("nats", "consumer"),
    "NATS_SINKS_NATS_SUBJECT": ("nats", "subject"),
    "NATS_SINKS_LOG_LEVEL": ("logging", "level"),
    "NATS_SINKS_SINK_TYPE": ("sink", "type"),
}


def _set_nested(config: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    current = config
    for part in path[:-1]:
        next_value = current.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ConfigurationError(f"cannot apply environment override for {'.'.join(path)}")
        current = next_value
    current[path[-1]] = value


def apply_environment_overrides(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Apply a small, explicit set of environment overrides."""

    config = copy.deepcopy(raw_config)
    for env_name, path in ENV_OVERRIDES.items():
        value = os.getenv(env_name)
        if value is not None:
            _set_nested(config, path, value)
    return config


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON configuration file and require an object at the root."""

    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"failed to read configuration file {file_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"configuration file {file_path} is not valid JSON") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration root must be a mapping")
    return raw


def load_config(path: str | Path, *, env_overrides: bool = True) -> AppConfig:
    """Load and validate an application configuration."""

    raw = load_json(path)
    if env_overrides:
        raw = apply_environment_overrides(raw)
    try:
        return AppConfig.model_validate(raw)
    except PydanticValidationError as exc:
        raise ConfigurationError(str(exc)) from exc


def _redact_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    key_lower = key.lower()
    if any(part in key_lower for part in SENSITIVE_KEY_PARTS):
        return "********"
    if key_lower in {"dsn", "url"} and isinstance(value, str) and "@" in value:
        return "********"
    return value


def redact_mapping(value: Any) -> Any:
    """Return a copy of a mapping/list tree with secret-looking values redacted."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: redact_mapping(_redact_value(str(key), item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value


def redacted_config(config: AppConfig) -> dict[str, Any]:
    """Return a redacted, serializable effective configuration."""

    return cast("dict[str, Any]", redact_mapping(config.model_dump(mode="json")))
