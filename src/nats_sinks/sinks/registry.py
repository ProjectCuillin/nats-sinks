# SPDX-License-Identifier: Apache-2.0
"""Safe sink registry.

Configuration selects sink types by name, but names are resolved through this
explicit allow-list registry rather than dynamic imports from untrusted config.
That keeps startup predictable, reduces attack surface, and lets the CLI report
clear errors when a sink type is unavailable.

Future sinks should register factories that accept a raw dictionary for their
own Pydantic validation.  The registry should remain simple and deterministic;
plugin discovery can be added later without weakening the safe default path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.sinks.base import Sink

SinkFactory = Callable[[dict[str, Any]], Sink]


class SinkRegistry:
    """Explicit allow-list registry for sink factories."""

    def __init__(self) -> None:
        self._factories: dict[str, SinkFactory] = {}

    def register(self, name: str, factory: SinkFactory) -> None:
        """Register a sink factory under a case-insensitive public name."""

        normalized = name.strip().lower()
        if not normalized:
            raise ConfigurationError("sink type name must not be empty")
        self._factories[normalized] = factory

    def create(self, name: str, config: dict[str, Any]) -> Sink:
        """Create a sink from validated configuration data."""

        normalized = name.strip().lower()
        try:
            factory = self._factories[normalized]
        except KeyError as exc:
            known = ", ".join(sorted(self._factories)) or "none"
            raise ConfigurationError(
                f"unknown sink type {name!r}; known sink types: {known}"
            ) from exc
        return factory(config)

    def names(self) -> tuple[str, ...]:
        """Return the registered sink type names in deterministic order."""

        return tuple(sorted(self._factories))
