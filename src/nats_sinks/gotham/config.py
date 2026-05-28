# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration for the experimental Palantir Gotham sink."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.payload import PayloadStorageMode

GothamTarget = Literal["object"]
GothamAuthMode = Literal["bearer_token_env", "oauth2_client_credentials"]
GothamExternalIdStrategy = Literal[
    "idempotency_key",
    "stream_sequence",
    "message_id",
    "payload_sha256",
]
GothamValidationMode = Literal["STRICT", "LENIENT"]

_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_GOTHAM_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*){1,31}$")
_PORTION_MARKING_RE = re.compile(r"^[A-Z0-9_.:-]{1,128}$")
_CONTROL_CHARACTER_CUTOFF = 32
_ASCII_DELETE = 127
_MAX_URL_PATH_LENGTH = 2048
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _contains_control_characters(value: str) -> bool:
    return any(
        ord(character) < _CONTROL_CHARACTER_CUTOFF or ord(character) == _ASCII_DELETE
        for character in value
    )


def _validate_env_name(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    if value != value.strip() or not _ENV_NAME_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be an uppercase environment variable name using letters, "
            "numbers, and underscores"
        )
    return value


def _validate_gotham_type(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field} must not be empty")
    if rendered != value or _contains_control_characters(value):
        raise ValueError(f"{field} must not contain padding or control characters")
    if not _GOTHAM_TYPE_RE.fullmatch(rendered):
        raise ValueError(f"{field} must be a dotted Gotham API type name")
    return rendered


def _validate_public_text(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field} must not be empty")
    if rendered != value or _contains_control_characters(value):
        raise ValueError(f"{field} must not contain padding or control characters")
    return rendered


def _validate_url(
    value: str,
    *,
    field: str,
    allow_http_for_local_testing: bool,
    allowed_hosts: tuple[str, ...],
    base_url_only: bool,
) -> str:
    rendered = value.strip()
    if not rendered:
        raise ValueError(f"{field} must not be empty")
    if rendered != value or _contains_control_characters(value):
        raise ValueError(f"{field} must not contain padding or control characters")

    parsed = urlsplit(rendered)
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    if scheme not in {"https", "http"}:
        raise ValueError(f"{field} must use https")
    if scheme == "http" and not (allow_http_for_local_testing and host in _LOOPBACK_HOSTS):
        raise ValueError(f"{field} must use https outside local loopback tests")
    if not host:
        raise ValueError(f"{field} must include a host")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not include userinfo")
    if parsed.fragment:
        raise ValueError(f"{field} must not include a fragment")
    if parsed.query:
        raise ValueError(f"{field} must not include a query string")
    if base_url_only and parsed.path not in {"", "/"}:
        raise ValueError(f"{field} must be a base URL without an API path")
    if len(parsed.path) > _MAX_URL_PATH_LENGTH:
        raise ValueError(f"{field} path is too long")
    if allowed_hosts and host not in allowed_hosts:
        raise ValueError(f"{field} host is not in endpoint_allowed_hosts")
    return rendered.rstrip("/") if base_url_only else rendered


class GothamSinkConfig(BaseModel):
    """Validated configuration for ``GothamSink``.

    The connector supports only Gotham RevDB object creation in this first
    experimental increment. Operators must map the generic nats-sinks event
    fields to Gotham ontology property types approved for their environment.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["gotham"] = "gotham"
    target: GothamTarget = "object"
    gotham_base_url: str
    object_type: str
    external_id_property_type: str
    subject_property_type: str
    payload_property_type: str
    payload_info_property_type: str | None = None
    metadata_property_type: str | None = None
    priority_property_type: str | None = None
    classification_property_type: str | None = None
    labels_property_type: str | None = None
    labels_list_property_type: str | None = None
    mission_metadata_property_type: str | None = None
    security_labels_property_type: str | None = None
    custody_property_type: str | None = None
    security_portion_markings: tuple[str, ...] = Field(default_factory=tuple)
    validation_mode: GothamValidationMode = "STRICT"
    auth_mode: GothamAuthMode = "bearer_token_env"
    bearer_token_env: str | None = None
    oauth2_token_url: str | None = None
    oauth2_client_id_env: str | None = None
    oauth2_client_secret_env: str | None = None
    oauth2_scope: str | None = None
    endpoint_allowed_hosts: tuple[str, ...] = Field(default_factory=tuple)
    allow_http_for_local_testing: bool = False
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=10)
    batch_size: int = Field(default=25, ge=1, le=250)
    max_object_bytes: int = Field(default=262_144, ge=1024, le=10_485_760)
    max_batch_bytes: int = Field(default=4_194_304, ge=1024, le=52_428_800)
    max_response_bytes: int = Field(default=65_536, ge=0, le=1_048_576)
    payload_mode: PayloadStorageMode = "json_or_envelope"
    external_id_strategy: GothamExternalIdStrategy = "idempotency_key"
    object_title_prefix: str = "nats-sinks event "
    max_title_length: int = Field(default=240, ge=32, le=1024)
    treat_conflict_as_duplicate: bool = False
    include_metadata: bool = True
    include_mission_metadata: bool = True
    include_security_labels: bool = True
    include_custody: bool = True

    def object_create_url(self) -> str:
        """Return the Gotham RevDB object-create endpoint for this config."""

        encoded_object_type = quote(self.object_type, safe="")
        return f"{self.gotham_base_url}/api/gotham/v1/objects/types/{encoded_object_type}"

    @field_validator(
        "bearer_token_env",
        "oauth2_client_id_env",
        "oauth2_client_secret_env",
    )
    @classmethod
    def validate_env_names(cls, value: str | None, info: object) -> str | None:
        field_name = str(getattr(info, "field_name", "field"))
        return _validate_env_name(value, field=field_name)

    @field_validator(
        "object_type",
        "external_id_property_type",
        "subject_property_type",
        "payload_property_type",
        "payload_info_property_type",
        "metadata_property_type",
        "priority_property_type",
        "classification_property_type",
        "labels_property_type",
        "labels_list_property_type",
        "mission_metadata_property_type",
        "security_labels_property_type",
        "custody_property_type",
    )
    @classmethod
    def validate_gotham_type_names(cls, value: str | None, info: object) -> str | None:
        field_name = str(getattr(info, "field_name", "field"))
        return _validate_gotham_type(value, field=field_name)

    @field_validator("endpoint_allowed_hosts", mode="before")
    @classmethod
    def normalize_endpoint_allowed_hosts(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("endpoint_allowed_hosts must be a list of hostnames")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("endpoint_allowed_hosts entries must be strings")
            host = item.strip().lower()
            if not host:
                raise ValueError("endpoint_allowed_hosts entries must not be empty")
            if host != item or _contains_control_characters(item):
                raise ValueError(
                    "endpoint_allowed_hosts entries must not contain padding or control characters"
                )
            if "/" in host or "@" in host or ":" in host:
                raise ValueError("endpoint_allowed_hosts entries must be hostnames only")
            if host in seen:
                raise ValueError(f"endpoint_allowed_hosts contains duplicate host {host!r}")
            seen.add(host)
            normalized.append(host)
        return tuple(normalized)

    @field_validator("security_portion_markings", mode="before")
    @classmethod
    def normalize_security_portion_markings(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("security_portion_markings must be a list of markings")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("security_portion_markings entries must be strings")
            marking = item.strip()
            if marking != item or not _PORTION_MARKING_RE.fullmatch(marking):
                raise ValueError("security_portion_markings entries must be simple markings")
            if marking in seen:
                raise ValueError(f"security_portion_markings contains duplicate {marking!r}")
            seen.add(marking)
            normalized.append(marking)
        return tuple(normalized)

    @field_validator("oauth2_scope", "object_title_prefix")
    @classmethod
    def validate_public_text(cls, value: str | None, info: object) -> str | None:
        field_name = str(getattr(info, "field_name", "field"))
        return _validate_public_text(value, field=field_name)

    @model_validator(mode="after")
    def validate_auth_endpoint_and_properties(self) -> GothamSinkConfig:
        """Validate cross-field authentication, endpoint, and ontology mapping."""

        self.gotham_base_url = _validate_url(
            self.gotham_base_url,
            field="gotham_base_url",
            allow_http_for_local_testing=self.allow_http_for_local_testing,
            allowed_hosts=self.endpoint_allowed_hosts,
            base_url_only=True,
        )
        if self.oauth2_token_url is not None:
            self.oauth2_token_url = _validate_url(
                self.oauth2_token_url,
                field="oauth2_token_url",
                allow_http_for_local_testing=self.allow_http_for_local_testing,
                allowed_hosts=self.endpoint_allowed_hosts,
                base_url_only=False,
            )

        if self.auth_mode == "bearer_token_env":
            if self.bearer_token_env is None:
                raise ValueError("bearer_token_env is required for bearer_token_env auth")
            if any(
                value is not None
                for value in (
                    self.oauth2_token_url,
                    self.oauth2_client_id_env,
                    self.oauth2_client_secret_env,
                    self.oauth2_scope,
                )
            ):
                raise ValueError("OAuth2 fields require auth_mode='oauth2_client_credentials'")
        else:
            missing = [
                name
                for name, value in (
                    ("oauth2_token_url", self.oauth2_token_url),
                    ("oauth2_client_id_env", self.oauth2_client_id_env),
                    ("oauth2_client_secret_env", self.oauth2_client_secret_env),
                )
                if value is None
            ]
            if missing:
                joined = ", ".join(missing)
                raise ValueError(f"{joined} required for oauth2_client_credentials auth")
            if self.bearer_token_env is not None:
                raise ValueError("bearer_token_env requires auth_mode='bearer_token_env'")

        property_types = tuple(
            value
            for value in (
                self.external_id_property_type,
                self.subject_property_type,
                self.payload_property_type,
                self.payload_info_property_type,
                self.metadata_property_type,
                self.priority_property_type,
                self.classification_property_type,
                self.labels_property_type,
                self.labels_list_property_type,
                self.mission_metadata_property_type,
                self.security_labels_property_type,
                self.custody_property_type,
            )
            if value is not None
        )
        duplicates = sorted(
            {
                property_type
                for property_type in property_types
                if property_types.count(property_type) > 1
            }
        )
        if duplicates:
            joined = ", ".join(duplicates)
            raise ValueError(f"Gotham property types must be unique: {joined}")
        if self.max_batch_bytes < self.max_object_bytes:
            raise ValueError("max_batch_bytes must be at least max_object_bytes")
        return self
