# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.skip(reason="requires an Oracle Database test container or service")
def test_oracle_idempotency_integration_placeholder() -> None:
    """Duplicate redelivery behavior is exercised in unit tests and documented for integration."""
