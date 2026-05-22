# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Safe sink connector descriptors and optional discovery helpers.

The production runtime resolves sink names through an explicit registry.  This
module adds a small connector descriptor around that registry so first-party
sinks, future first-party sinks, and carefully approved third-party packages
can describe their capabilities without allowing arbitrary imports from JSON
configuration.

Entry-point discovery is intentionally opt-in and allow-list based.  Loading a
Python entry point executes code from the installed distribution, so it must be
treated as a supply-chain trust decision.  The helpers below load only names
that an operator explicitly allow-lists and require a typed `SinkConnector`
descriptor rather than accepting raw module paths from configuration.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Literal, cast

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.sinks.base import Sink

SinkFactory = Callable[[dict[str, Any]], Sink]
SinkConnectorStatus = Literal["production", "experimental", "planned", "third_party"]

SINK_CONNECTOR_API_VERSION = "1"
SINK_CONNECTOR_ENTRY_POINT_GROUP = "nats_sinks.sinks"
CONNECTOR_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_CONNECTOR_SUMMARY_LENGTH = 500
MAX_CONNECTOR_FIELD_LENGTH = 255


@dataclass(frozen=True)
class SinkConnector:
    """Metadata and factory for one sink connector.

    `factory` is the only executable part of the descriptor.  It receives the
    selected raw `sink` JSON object and returns an object implementing the
    `Sink` protocol.  The remaining fields are intentionally small and
    operator-facing so CLI output, docs, and compatibility tests can describe a
    connector without importing destination internals elsewhere.
    """

    name: str
    factory: SinkFactory
    summary: str
    status: SinkConnectorStatus = "production"
    api_version: str = SINK_CONNECTOR_API_VERSION
    built_in: bool = False
    production_ready: bool = False
    requires_extra: str | None = None
    documentation: str | None = None
    certification: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Normalize and validate connector metadata at construction time."""

        object.__setattr__(self, "name", normalize_connector_name(self.name))
        if not callable(self.factory):
            raise ConfigurationError(f"sink connector {self.name!r} factory must be callable")
        if not self.summary.strip():
            raise ConfigurationError(f"sink connector {self.name!r} summary must not be empty")
        if len(self.summary) > MAX_CONNECTOR_SUMMARY_LENGTH:
            raise ConfigurationError(
                f"sink connector {self.name!r} summary must be at most "
                f"{MAX_CONNECTOR_SUMMARY_LENGTH} characters"
            )
        if self.api_version != SINK_CONNECTOR_API_VERSION:
            raise ConfigurationError(
                f"sink connector {self.name!r} uses unsupported connector API "
                f"{self.api_version!r}; supported API is {SINK_CONNECTOR_API_VERSION!r}"
            )
        _validate_optional_text(self.requires_extra, field="requires_extra", name=self.name)
        _validate_optional_text(self.documentation, field="documentation", name=self.name)
        _validate_certification(self.certification, name=self.name)

    def public_record(self) -> dict[str, object]:
        """Return non-sensitive metadata suitable for CLI and documentation use."""

        return {
            "name": self.name,
            "summary": self.summary,
            "status": self.status,
            "api_version": self.api_version,
            "built_in": self.built_in,
            "production_ready": self.production_ready,
            "requires_extra": self.requires_extra,
            "documentation": self.documentation,
            "certification": list(self.certification),
        }


def normalize_connector_name(name: str) -> str:
    """Return a canonical sink connector name or raise `ConfigurationError`."""

    normalized = name.strip().lower()
    if not CONNECTOR_NAME_RE.fullmatch(normalized):
        raise ConfigurationError(
            "sink connector names must start with a lowercase letter and contain only "
            "lowercase letters, digits, '_' or '-'"
        )
    return normalized


def _validate_optional_text(value: str | None, *, field: str, name: str) -> None:
    """Validate small optional descriptor text fields."""

    if value is None:
        return
    if value.strip() != value or not value:
        raise ConfigurationError(f"sink connector {name!r} {field} must not be blank")
    if len(value) > MAX_CONNECTOR_FIELD_LENGTH:
        raise ConfigurationError(
            f"sink connector {name!r} {field} must be at most "
            f"{MAX_CONNECTOR_FIELD_LENGTH} characters"
        )


def _validate_certification(values: tuple[str, ...], *, name: str) -> None:
    """Validate certification labels used by connector docs and tests."""

    for value in values:
        if value.strip() != value or not value:
            raise ConfigurationError(
                f"sink connector {name!r} certification entries must not be blank"
            )
        if len(value) > MAX_CONNECTOR_FIELD_LENGTH:
            raise ConfigurationError(
                f"sink connector {name!r} certification entries must be at most "
                f"{MAX_CONNECTOR_FIELD_LENGTH} characters"
            )


def load_entry_point_connectors(
    *,
    allowed_names: Iterable[str],
    require_production_ready: bool = True,
    entry_point_group: str = SINK_CONNECTOR_ENTRY_POINT_GROUP,
    entry_points_provider: Callable[[], Iterable[object]] | None = None,
) -> tuple[SinkConnector, ...]:
    """Load explicitly allowed sink connectors from Python entry points.

    The function never loads every installed entry point.  It first normalizes
    the operator-provided allow-list and only calls `load()` on matching names.
    This prevents arbitrary installed packages from becoming selectable merely
    because they advertise the same entry-point group.
    """

    allowed = {normalize_connector_name(name) for name in allowed_names}
    if not allowed:
        return ()

    connectors: list[SinkConnector] = []
    seen: set[str] = set()
    for entry_point in sorted(
        _sink_entry_points(entry_point_group, provider=entry_points_provider),
        key=lambda item: _entry_point_name(item),
    ):
        entry_name = normalize_connector_name(_entry_point_name(entry_point))
        if entry_name not in allowed:
            continue
        connector = _load_connector_from_entry_point(entry_point)
        if connector.name != entry_name:
            raise ConfigurationError(
                f"sink connector entry point {entry_name!r} returned connector {connector.name!r}"
            )
        if connector.built_in:
            raise ConfigurationError(
                f"external sink connector {connector.name!r} must not claim built-in status"
            )
        if require_production_ready and not connector.production_ready:
            raise ConfigurationError(
                f"sink connector {connector.name!r} is not marked production-ready"
            )
        if connector.name in seen:
            raise ConfigurationError(f"duplicate sink connector entry point {connector.name!r}")
        seen.add(connector.name)
        connectors.append(connector)

    missing = tuple(sorted(allowed - seen))
    if missing:
        joined = ", ".join(missing)
        raise ConfigurationError(f"allowed sink connector(s) not installed: {joined}")
    return tuple(connectors)


def _sink_entry_points(
    entry_point_group: str,
    *,
    provider: Callable[[], Iterable[object]] | None,
) -> tuple[object, ...]:
    """Return entry points for the configured group using stdlib metadata."""

    if provider is not None:
        return tuple(provider())
    discovered = metadata.entry_points()
    select = getattr(discovered, "select", None)
    if callable(select):
        return tuple(select(group=entry_point_group))
    legacy = cast(dict[str, Iterable[object]], discovered)
    return tuple(legacy.get(entry_point_group, ()))


def _entry_point_name(entry_point: object) -> str:
    """Return the name from an importlib-style entry-point object."""

    raw_name = getattr(entry_point, "name", None)
    if not isinstance(raw_name, str):
        raise ConfigurationError("sink connector entry point is missing a string name")
    return raw_name


def _load_connector_from_entry_point(entry_point: object) -> SinkConnector:
    """Load and validate one entry-point object."""

    load = getattr(entry_point, "load", None)
    if not callable(load):
        raise ConfigurationError(
            f"sink connector entry point {_entry_point_name(entry_point)!r} has no load() method"
        )
    loaded = load()
    if not isinstance(loaded, SinkConnector):
        raise ConfigurationError(
            f"sink connector entry point {_entry_point_name(entry_point)!r} must return "
            "a SinkConnector descriptor"
        )
    return loaded
