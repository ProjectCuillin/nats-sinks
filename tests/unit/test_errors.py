# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from nats_sinks import (
    DestinationUnavailableError,
    NatsSinksError,
    PermanentSinkError,
    SerializationError,
    TemporarySinkError,
    ValidationError,
)


def test_error_hierarchy_classifies_failures() -> None:
    assert issubclass(DestinationUnavailableError, TemporarySinkError)
    assert issubclass(SerializationError, PermanentSinkError)
    assert issubclass(ValidationError, PermanentSinkError)
    assert issubclass(TemporarySinkError, NatsSinksError)
