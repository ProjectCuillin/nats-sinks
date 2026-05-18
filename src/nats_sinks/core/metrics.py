# SPDX-License-Identifier: Apache-2.0
"""Small metrics abstraction for first-release instrumentation.

The runner records events through a tiny protocol instead of binding the core
package to a metrics backend.  This keeps the package suitable for libraries,
CLIs, containers, and embedded services that may already use Prometheus,
OpenTelemetry, StatsD, or another telemetry stack.

`InMemoryMetrics` is useful for tests and local embedding.  `NoopMetrics` is
the default when metrics are disabled.  Future exporters should implement the
same protocol while preserving metric names documented in the operations guide.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol


class MetricsRecorder(Protocol):
    """Metrics interface used by the runner."""

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a counter."""

    def observe(self, name: str, value: float) -> None:
        """Observe a floating-point value."""

    def set_value(self, name: str, value: float) -> None:
        """Set a gauge-like value."""


@dataclass(slots=True)
class InMemoryMetrics:
    """Deterministic in-memory metrics recorder useful for tests and embedding."""

    counters: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    observations: defaultdict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    gauges: dict[str, float] = field(default_factory=dict)

    def increment(self, name: str, value: int = 1) -> None:
        """Increase a named counter by `value`."""

        self.counters[name] += value

    def observe(self, name: str, value: float) -> None:
        """Record one floating-point observation for a named metric."""

        self.observations[name].append(value)

    def set_value(self, name: str, value: float) -> None:
        """Set the latest value for a named gauge-style metric."""

        self.gauges[name] = value

    def mark_success(self) -> None:
        """Record the current wall-clock time as the last successful write."""

        self.set_value("last_success_timestamp", time.time())


class NoopMetrics:
    """Metrics recorder that intentionally does nothing."""

    def increment(self, name: str, value: int = 1) -> None:
        """Accept counter updates when metrics collection is disabled."""

        del name, value

    def observe(self, name: str, value: float) -> None:
        """Accept observations when metrics collection is disabled."""

        del name, value

    def set_value(self, name: str, value: float) -> None:
        """Accept gauge updates when metrics collection is disabled."""

        del name, value
