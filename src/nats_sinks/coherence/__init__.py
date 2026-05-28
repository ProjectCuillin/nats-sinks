# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle Coherence Community Edition sink.

The package is safe to import without the optional Coherence Python client
installed.  The driver is imported lazily when ``CoherenceSink.start`` opens a
real session.
"""

from nats_sinks.coherence.config import (
    CoherenceDuplicatePolicy,
    CoherenceDurabilityMode,
    CoherenceKeyStrategy,
    CoherenceSerializer,
    CoherenceSinkConfig,
    CoherenceStorageKind,
)
from nats_sinks.coherence.mapping import (
    COHERENCE_EVENT_SCHEMA,
    COHERENCE_EVENT_SCHEMA_VERSION,
    coherence_key_for_envelope,
    coherence_value_for_envelope,
)
from nats_sinks.coherence.sink import CoherenceSessionFactory, CoherenceSink

__all__ = [
    "COHERENCE_EVENT_SCHEMA",
    "COHERENCE_EVENT_SCHEMA_VERSION",
    "CoherenceDuplicatePolicy",
    "CoherenceDurabilityMode",
    "CoherenceKeyStrategy",
    "CoherenceSerializer",
    "CoherenceSessionFactory",
    "CoherenceSink",
    "CoherenceSinkConfig",
    "CoherenceStorageKind",
    "coherence_key_for_envelope",
    "coherence_value_for_envelope",
]
