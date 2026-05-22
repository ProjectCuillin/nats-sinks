# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Small validation helpers.

Validation functions in this module are shared by configuration and future sink
code when a full Pydantic model would be heavier than necessary.  Keeping these
helpers in core gives the project one place for clear, framework-defined error
messages instead of ad hoc `ValueError` usage spread across modules.

The helpers should stay side-effect free and deterministic.  They validate
local values only and never perform network calls, file-system access, or
destination-specific checks.
"""

from __future__ import annotations

from nats_sinks.core.errors import ConfigurationError


def ensure_not_empty(value: str, *, name: str) -> str:
    """Validate that a string configuration value is not empty."""

    if not value.strip():
        raise ConfigurationError(f"{name} must not be empty")
    return value
