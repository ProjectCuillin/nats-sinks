# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Sink contracts and registry.

The sinks package contains the destination-facing extension points used by the
core runtime and by future sink modules.  It exposes the minimal `Sink` protocol
plus optional protocols for health checks, schema management, and flushing.

This package has no destination-specific dependencies.  Oracle, File, future
Oracle Cloud, Palantir, HTTP, object-storage, database, and search connectors
should depend on these contracts rather than importing core runner internals.
"""

from nats_sinks.sinks.base import FlushableSink, HealthCheckableSink, SchemaAwareSink, Sink
from nats_sinks.sinks.connectors import (
    SINK_CONNECTOR_API_VERSION,
    SINK_CONNECTOR_ENTRY_POINT_GROUP,
    SinkConnector,
    SinkConnectorStatus,
    load_entry_point_connectors,
    normalize_connector_name,
)
from nats_sinks.sinks.registry import SinkFactory, SinkRegistry

__all__ = [
    "SINK_CONNECTOR_API_VERSION",
    "SINK_CONNECTOR_ENTRY_POINT_GROUP",
    "FlushableSink",
    "HealthCheckableSink",
    "SchemaAwareSink",
    "Sink",
    "SinkConnector",
    "SinkConnectorStatus",
    "SinkFactory",
    "SinkRegistry",
    "load_entry_point_connectors",
    "normalize_connector_name",
]
