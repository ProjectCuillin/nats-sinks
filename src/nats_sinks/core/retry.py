# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
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

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from secrets import randbelow
from typing import Literal

RetryBackoffMode = Literal["fixed", "linear", "exponential"]
RetryJitterMode = Literal["none", "full", "equal"]


def _default_random_fraction() -> float:
    """Return a random fraction for jitter without using non-secure randomness.

    Retry jitter is not cryptographic material, but using `secrets` avoids
    accidental reuse of this helper in security-sensitive code paths with a
    weaker random source.  Tests can inject a deterministic callable through
    `random_fraction`.
    """

    return randbelow(1_000_000) / 1_000_000


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy used by the runtime.

    `attempt` values are one-based and normally come from JetStream
    `num_delivered` metadata.  The first failed delivery therefore uses
    attempt `1`, the next redelivery uses attempt `2`, and so on.

    The policy calculates the delay used for delayed NAK operations.  It never
    ACKs, sleeps, publishes to DLQ, or decides whether a sink failure is
    temporary or permanent; those delivery semantics remain in the runner.
    """

    max_retries: int = 5
    backoff_ms: int = 1000
    max_backoff_ms: int = 60_000
    backoff_mode: RetryBackoffMode = "exponential"
    backoff_multiplier: float = 2.0
    jitter: RetryJitterMode = "full"
    random_fraction: Callable[[], float] | None = None

    def __post_init__(self) -> None:
        """Validate public API construction as strictly as JSON configuration."""

        if isinstance(self.max_retries, bool) or self.max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to zero")
        if isinstance(self.backoff_ms, bool) or self.backoff_ms < 0:
            raise ValueError("backoff_ms must be greater than or equal to zero")
        if isinstance(self.max_backoff_ms, bool) or self.max_backoff_ms < 0:
            raise ValueError("max_backoff_ms must be greater than or equal to zero")
        if self.max_backoff_ms < self.backoff_ms:
            raise ValueError("max_backoff_ms must be greater than or equal to backoff_ms")
        if self.backoff_mode not in {"fixed", "linear", "exponential"}:
            raise ValueError("backoff_mode must be fixed, linear, or exponential")
        if self.jitter not in {"none", "full", "equal"}:
            raise ValueError("jitter must be none, full, or equal")
        if not isfinite(self.backoff_multiplier) or self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be finite and greater than or equal to 1.0")

    def should_retry(self, attempt: int) -> bool:
        """Return true if the given one-based attempt may receive an active retry."""

        return max(attempt, 1) <= self.max_retries

    def raw_backoff_seconds(self, attempt: int) -> float:
        """Return the capped delay before jitter is applied."""

        one_based_attempt = max(attempt, 1)
        if self.backoff_mode == "fixed":
            delay_ms = float(self.backoff_ms)
        elif self.backoff_mode == "linear":
            delay_ms = float(self.backoff_ms * one_based_attempt)
        else:
            try:
                delay_ms = self.backoff_ms * (self.backoff_multiplier ** (one_based_attempt - 1))
            except OverflowError:
                delay_ms = float(self.max_backoff_ms)
            if not isfinite(delay_ms):
                delay_ms = float(self.max_backoff_ms)
        return min(delay_ms, self.max_backoff_ms) / 1000

    def backoff_seconds(self, attempt: int) -> float:
        """Return the final retry delay in seconds after configured jitter.

        Supported jitter modes are:

        - `none`: use the calculated delay exactly,
        - `full`: choose a value between zero and the calculated delay,
        - `equal`: choose a value between half-delay and full-delay.
        """

        delay = self.raw_backoff_seconds(attempt)
        if delay <= 0 or self.jitter == "none":
            return delay

        random_fraction = self.random_fraction or _default_random_fraction
        candidate = random_fraction()
        fraction = candidate if isfinite(candidate) else 0.0
        fraction = min(max(fraction, 0.0), 1.0)
        if self.jitter == "equal":
            return (delay / 2) + ((delay / 2) * fraction)
        return delay * fraction
