# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.skip(reason="requires a local NATS JetStream service")
def test_nats_jetstream_runner_integration_placeholder() -> None:
    """Covered by docker-compose examples; enable when service orchestration is available."""
