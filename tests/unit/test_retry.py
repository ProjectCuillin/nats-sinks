# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from nats_sinks.core.retry import RetryPolicy


def test_retry_policy_is_bounded() -> None:
    policy = RetryPolicy(max_retries=2, backoff_ms=100)

    assert policy.should_retry(1)
    assert policy.should_retry(2)
    assert not policy.should_retry(3)
    assert policy.backoff_seconds(3) == 0.3
