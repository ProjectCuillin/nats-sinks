# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Safe sink registry.

Configuration selects sink types by name, but names are resolved through this
explicit allow-list registry rather than dynamic imports from untrusted config.
That keeps startup predictable, reduces attack surface, and lets the CLI report
clear errors when a sink type is unavailable.

Future sinks should register connector descriptors that accept a raw dictionary
for their own Pydantic validation.  The registry remains deterministic and
does not import arbitrary modules from configuration.  Optional entry-point
discovery is handled by `nats_sinks.sinks.connectors` and remains allow-list
based.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.sinks.base import Sink
from nats_sinks.sinks.connectors import SinkConnector

SinkFactory = Callable[[dict[str, Any]], Sink]


class SinkRegistry:
    """Explicit allow-list registry for sink connector factories."""

    def __init__(self) -> None:
        self._connectors: dict[str, SinkConnector] = {}

    def register(self, name: str, factory: SinkFactory) -> None:
        """Register a sink factory under a case-insensitive public name.

        This compatibility helper remains available for tests and embedded
        applications.  New sink modules should prefer `register_connector()` so
        they can provide metadata and certification state.
        """

        self.register_connector(
            SinkConnector(
                name=name,
                factory=factory,
                summary=f"{name.strip().lower()} sink connector",
            )
        )

    def register_connector(self, connector: SinkConnector) -> None:
        """Register a validated sink connector descriptor."""

        if connector.name in self._connectors:
            raise ConfigurationError(f"sink connector {connector.name!r} is already registered")
        self._connectors[connector.name] = connector

    def create(self, name: str, config: dict[str, Any]) -> Sink:
        """Create a sink from validated configuration data."""

        normalized = name.strip().lower()
        try:
            connector = self._connectors[normalized]
        except KeyError as exc:
            known = ", ".join(sorted(self._connectors)) or "none"
            raise ConfigurationError(
                f"unknown sink type {name!r}; known sink types: {known}"
            ) from exc
        return connector.factory(config)

    def connector(self, name: str) -> SinkConnector:
        """Return connector metadata for one registered sink type."""

        normalized = name.strip().lower()
        try:
            return self._connectors[normalized]
        except KeyError as exc:
            known = ", ".join(sorted(self._connectors)) or "none"
            raise ConfigurationError(
                f"unknown sink connector {name!r}; known sink connectors: {known}"
            ) from exc

    def connectors(self) -> tuple[SinkConnector, ...]:
        """Return registered connector descriptors in deterministic order."""

        return tuple(self._connectors[name] for name in sorted(self._connectors))

    def names(self) -> tuple[str, ...]:
        """Return the registered sink type names in deterministic order."""

        return tuple(sorted(self._connectors))
