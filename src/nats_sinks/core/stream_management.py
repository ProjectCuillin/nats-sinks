# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Offline JetStream stream-management planning helpers.

The sink runtime deliberately avoids broad NATS administration privileges.
Runtime workers fetch from an existing stream and durable consumer, write to a
destination, and ACK only after durable success.  Creating or updating streams
is a different operational role: it belongs to a NATS administrator, Terraform
module, release pipeline, or another explicitly approved control-plane tool.

This module provides a narrow middle ground.  It can build a public-safe plan
from the same JSON configuration that drives the worker, but it never connects
to NATS and it never mutates server state.  The plan helps operators review
retention, discard behavior, storage type, replicas, duplicate-window policy,
and permission boundaries before they apply changes with their normal NATS
administration tooling.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Literal

from nats_sinks.core.config import AppConfig
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.subjects import validate_subject_pattern

StreamRetentionPolicy = Literal["limits", "interest", "workqueue"]
StreamDiscardPolicy = Literal["old", "new"]
StreamStorageType = Literal["file", "memory"]

RETENTION_POLICIES: frozenset[str] = frozenset({"limits", "interest", "workqueue"})
DISCARD_POLICIES: frozenset[str] = frozenset({"old", "new"})
STORAGE_TYPES: frozenset[str] = frozenset({"file", "memory"})
MAX_STREAM_NAME_LENGTH = 128
MAX_STREAM_REPLICAS = 5
MAX_DUPLICATE_WINDOW_SECONDS = 604_800
DEFAULT_DUPLICATE_WINDOW_SECONDS = 120
ASCII_CONTROL_MAX = 31


def _normalize_choice(value: object, *, allowed: frozenset[str], field: str) -> str:
    """Normalize a CLI/config choice with a small explicit allow list."""

    if not isinstance(value, str):
        raise ConfigurationError(f"{field} must be a string")
    normalized = value.strip().casefold()
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ConfigurationError(f"{field} must be one of: {allowed_values}")
    return normalized


def validate_stream_name(value: object) -> str:
    """Validate a JetStream stream name for local planning output.

    The helper is intentionally conservative.  It rejects control characters,
    whitespace, path separators, and subject wildcards because stream names are
    commonly copied into NATS CLI commands, permission templates, dashboards,
    and runbooks.  Rejecting ambiguous names locally is safer than generating a
    misleading administrative plan.
    """

    if not isinstance(value, str):
        raise ConfigurationError("nats.stream must be a string")
    stream = value.strip()
    if not stream or stream != value:
        raise ConfigurationError("nats.stream must not be empty or padded")
    if len(stream) > MAX_STREAM_NAME_LENGTH:
        raise ConfigurationError(f"nats.stream must not exceed {MAX_STREAM_NAME_LENGTH} characters")
    unsafe = {".", "*", ">", "/", "\\"}
    if any(
        character.isspace() or ord(character) <= ASCII_CONTROL_MAX or character in unsafe
        for character in stream
    ):
        raise ConfigurationError(
            "nats.stream must not contain whitespace, control characters, '.', '*', '>', or slashes"
        )
    return stream


@dataclass(frozen=True)
class StreamManagementOptions:
    """Operator-selected stream settings used to produce a reviewable plan."""

    retention: str = "limits"
    discard: str = "old"
    storage: str = "file"
    replicas: int = 1
    duplicate_window_seconds: int = DEFAULT_DUPLICATE_WINDOW_SECONDS

    def __post_init__(self) -> None:
        """Validate options without opening any NATS connection."""

        object.__setattr__(
            self,
            "retention",
            _normalize_choice(
                self.retention,
                allowed=RETENTION_POLICIES,
                field="retention",
            ),
        )
        object.__setattr__(
            self,
            "discard",
            _normalize_choice(self.discard, allowed=DISCARD_POLICIES, field="discard"),
        )
        object.__setattr__(
            self,
            "storage",
            _normalize_choice(self.storage, allowed=STORAGE_TYPES, field="storage"),
        )
        if isinstance(self.replicas, bool) or not isinstance(self.replicas, int):
            raise ConfigurationError("replicas must be an integer")
        if self.replicas < 1 or self.replicas > MAX_STREAM_REPLICAS:
            raise ConfigurationError(f"replicas must be between 1 and {MAX_STREAM_REPLICAS}")
        if isinstance(self.duplicate_window_seconds, bool) or not isinstance(
            self.duplicate_window_seconds, int
        ):
            raise ConfigurationError("duplicate_window_seconds must be an integer")
        if (
            self.duplicate_window_seconds < 1
            or self.duplicate_window_seconds > MAX_DUPLICATE_WINDOW_SECONDS
        ):
            raise ConfigurationError(
                f"duplicate_window_seconds must be between 1 and {MAX_DUPLICATE_WINDOW_SECONDS}"
            )


@dataclass(frozen=True)
class StreamManagementPlan:
    """A sanitized stream-preparation plan for operators and automation."""

    stream: str
    subjects: tuple[str, ...]
    durable_consumer: str
    settings: StreamManagementOptions
    runtime_permissions: tuple[str, ...]
    administration_permissions: tuple[str, ...]
    nats_cli_example: str
    notes: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-serializable representation."""

        return {
            "stream": self.stream,
            "subjects": list(self.subjects),
            "durable_consumer": self.durable_consumer,
            "recommended_stream_settings": {
                "retention": self.settings.retention,
                "discard": self.settings.discard,
                "storage": self.settings.storage,
                "replicas": self.settings.replicas,
                "duplicate_window_seconds": self.settings.duplicate_window_seconds,
            },
            "runtime_permissions": list(self.runtime_permissions),
            "administration_permissions": list(self.administration_permissions),
            "nats_cli_example": self.nats_cli_example,
            "notes": list(self.notes),
            "warnings": list(self.warnings),
        }


def _subjects_for_config(config: AppConfig) -> tuple[str, ...]:
    """Return the subject set that should be present on the stream."""

    configured = config.consumer_management.filter_subjects or (config.nats.subject,)
    return tuple(validate_subject_pattern(subject) for subject in configured)


def _nats_cli_example(
    *,
    stream: str,
    subjects: tuple[str, ...],
    options: StreamManagementOptions,
) -> str:
    """Render a copyable NATS CLI example using safely quoted values."""

    command = [
        "nats",
        "stream",
        "add",
        stream,
        "--subjects",
        ",".join(subjects),
        "--retention",
        options.retention,
        "--discard",
        options.discard,
        "--storage",
        options.storage,
        "--replicas",
        str(options.replicas),
        "--dupe-window",
        f"{options.duplicate_window_seconds}s",
    ]
    return " ".join(shlex.quote(part) for part in command)


def _warnings(options: StreamManagementOptions) -> tuple[str, ...]:
    """Explain settings that deserve explicit operator review."""

    warnings: list[str] = []
    if options.replicas == 1:
        warnings.append(
            "replicas=1 is suitable for local or edge deployments but does not provide "
            "cluster-level stream redundancy"
        )
    if options.storage == "memory":
        warnings.append(
            "memory storage can be fast but does not survive server restart the same way file "
            "storage does"
        )
    if options.discard == "new":
        warnings.append(
            "discard=new rejects new publishes when limits are reached; confirm producers and "
            "operators can observe and handle that backpressure"
        )
    if options.retention == "interest":
        warnings.append(
            "interest retention removes messages after all matching consumers have acknowledged "
            "them; review carefully before replay-oriented sink deployments"
        )
    if options.retention == "workqueue":
        warnings.append(
            "workqueue retention is for queue-style processing and should not be used when "
            "multiple independent sink consumers need the same event"
        )
    if options.duplicate_window_seconds < DEFAULT_DUPLICATE_WINDOW_SECONDS:
        warnings.append(
            "duplicate_window_seconds is below the common 120 second default; producer retry "
            "windows may need a larger duplicate-detection window"
        )
    return tuple(warnings)


def build_stream_management_plan(
    config: AppConfig,
    options: StreamManagementOptions | None = None,
) -> StreamManagementPlan:
    """Build an offline stream-preparation plan from validated app config.

    The function is side-effect free.  It reads only the already-loaded
    configuration object and returns guidance that can be rendered by the CLI,
    tests, or future automation.  It does not resolve credentials, open NATS
    connections, create streams, update streams, or create consumers.
    """

    selected = options or StreamManagementOptions()
    stream = validate_stream_name(config.nats.stream)
    subjects = _subjects_for_config(config)
    consumer = config.nats.consumer

    runtime_permissions = (
        f"$JS.API.CONSUMER.MSG.NEXT.{stream}.{consumer}",
        f"$JS.API.CONSUMER.INFO.{stream}.{consumer}",
        f"$JS.ACK.{stream}.{consumer}.>",
        "_INBOX.>",
    )
    administration_permissions = (
        f"$JS.API.STREAM.CREATE.{stream}",
        f"$JS.API.STREAM.UPDATE.{stream}",
        f"$JS.API.STREAM.INFO.{stream}",
        "$JS.API.STREAM.NAMES",
        f"$JS.API.CONSUMER.DURABLE.CREATE.{stream}.{consumer}",
    )
    notes = (
        "This plan is offline guidance only; nats-sinks does not connect to NATS or "
        "modify stream state when generating it.",
        "Use a separate administrative identity to apply stream or consumer changes.",
        "The sink runtime should normally keep only pull, ACK, INFO, inbox, and optional "
        "DLQ publish permissions.",
        "ACK behavior remains commit-then-acknowledge; stream management does not allow early ACK.",
    )
    return StreamManagementPlan(
        stream=stream,
        subjects=subjects,
        durable_consumer=consumer,
        settings=selected,
        runtime_permissions=runtime_permissions,
        administration_permissions=administration_permissions,
        nats_cli_example=_nats_cli_example(stream=stream, subjects=subjects, options=selected),
        notes=notes,
        warnings=_warnings(selected),
    )
