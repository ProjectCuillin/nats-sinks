# SPDX-License-Identifier: Apache-2.0
"""Local filesystem sink for nats-sinks.

The file sink is a production sink for deployments that need a simple durable
local handoff format.  It writes one JSON document per JetStream message using
deterministic file names, temporary files, flush/fsync, and atomic placement in
the destination directory.

The sink remains inside the same framework boundary as every other backend:
it receives `NatsEnvelope` objects, writes them durably, and never acknowledges
JetStream messages itself.  The core runner ACKs only after `FileSink` returns
success.
"""

from nats_sinks.file.config import (
    FileDuplicatePolicy,
    FileFilenameStrategy,
    FileSinkConfig,
    FileWriteMode,
)
from nats_sinks.file.sink import FileSink

__all__ = [
    "FileDuplicatePolicy",
    "FileFilenameStrategy",
    "FileSink",
    "FileSinkConfig",
    "FileWriteMode",
]
