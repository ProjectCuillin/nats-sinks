# SPDX-License-Identifier: Apache-2.0
"""Retry policy helpers.

Retry behavior in nats-sinks must stay explicit and bounded.  The framework
prefers redelivery over silent loss, but it should not hide repeated failures
behind unbounded loops or surprise sleeps.  This module provides a small policy
object used by the runtime and tests.

The policy does not decide whether an error is temporary or permanent.  Sinks
translate destination-specific failures into framework errors, and the runner
combines that classification with delivery settings such as NAK delay or
leaving messages unacked for JetStream redelivery.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Simple bounded retry policy used by the runtime."""

    max_retries: int = 5
    backoff_ms: int = 1000

    def should_retry(self, attempt: int) -> bool:
        """Return true if the given one-based attempt may be retried."""

        return attempt <= self.max_retries

    def backoff_seconds(self, attempt: int) -> float:
        """Return linear backoff in seconds for a retry attempt."""

        multiplier = max(attempt, 1)
        return (self.backoff_ms * multiplier) / 1000
