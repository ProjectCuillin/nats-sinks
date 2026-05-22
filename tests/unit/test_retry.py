# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from nats_sinks.core.retry import RetryPolicy


def test_retry_policy_is_bounded() -> None:
    policy = RetryPolicy(max_retries=2, backoff_ms=100, jitter="none")

    assert policy.should_retry(1)
    assert policy.should_retry(2)
    assert not policy.should_retry(3)
    assert policy.backoff_seconds(3) == 0.4


def test_retry_policy_can_disable_active_retries() -> None:
    policy = RetryPolicy(max_retries=0)

    assert not policy.should_retry(0)
    assert not policy.should_retry(1)


def test_retry_policy_supports_fixed_backoff() -> None:
    policy = RetryPolicy(backoff_ms=250, backoff_mode="fixed", jitter="none")

    assert policy.backoff_seconds(1) == 0.25
    assert policy.backoff_seconds(5) == 0.25


def test_retry_policy_supports_linear_backoff() -> None:
    policy = RetryPolicy(backoff_ms=100, backoff_mode="linear", jitter="none")

    assert policy.backoff_seconds(1) == 0.1
    assert policy.backoff_seconds(3) == 0.3


def test_retry_policy_supports_exponential_backoff_with_cap() -> None:
    policy = RetryPolicy(
        backoff_ms=100,
        backoff_mode="exponential",
        backoff_multiplier=2.0,
        max_backoff_ms=250,
        jitter="none",
    )

    assert policy.backoff_seconds(1) == 0.1
    assert policy.backoff_seconds(2) == 0.2
    assert policy.backoff_seconds(3) == 0.25


def test_retry_policy_supports_full_jitter() -> None:
    policy = RetryPolicy(
        backoff_ms=1000,
        backoff_mode="fixed",
        jitter="full",
        random_fraction=lambda: 0.25,
    )

    assert policy.backoff_seconds(1) == 0.25


def test_retry_policy_supports_equal_jitter() -> None:
    policy = RetryPolicy(
        backoff_ms=1000,
        backoff_mode="fixed",
        jitter="equal",
        random_fraction=lambda: 0.25,
    )

    assert policy.backoff_seconds(1) == 0.625


def test_retry_policy_clamps_invalid_jitter_fraction() -> None:
    policy = RetryPolicy(
        backoff_ms=1000,
        backoff_mode="fixed",
        jitter="full",
        random_fraction=lambda: float("nan"),
    )

    assert policy.backoff_seconds(1) == 0.0
