# SPDX-License-Identifier: Apache-2.0
"""Sink contracts and registry.

The sinks package contains the destination-facing extension points used by the
core runtime and by future sink modules.  It exposes the minimal `Sink` protocol
plus optional protocols for health checks, schema management, and flushing.

This package has no destination-specific dependencies.  Oracle, Postgres, HTTP,
file, S3, Kafka, and other implementations should depend on these contracts
rather than importing core runner internals.
"""

from nats_sinks.sinks.base import FlushableSink, HealthCheckableSink, SchemaAwareSink, Sink
from nats_sinks.sinks.registry import SinkFactory, SinkRegistry

__all__ = [
    "FlushableSink",
    "HealthCheckableSink",
    "SchemaAwareSink",
    "Sink",
    "SinkFactory",
    "SinkRegistry",
]
