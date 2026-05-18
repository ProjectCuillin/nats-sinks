# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.skip(reason="requires a local NATS JetStream service")
def test_dlq_integration_placeholder() -> None:
    """DLQ publication ordering is covered by unit tests; this is for real JetStream validation."""
