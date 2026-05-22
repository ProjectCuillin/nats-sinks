# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Encrypted local spool sink for disconnected nats-sinks deployments.

The spool sink is a production-oriented edge custody option.  It stores
normalized `NatsEnvelope` records as bounded local files and, by default,
encrypts each replay record before it reaches disk.  When configured as the
active sink, JetStream ACK happens after the local spool commit.  Forwarding
from spool to Oracle, file, or another future sink is a separate replay phase.
"""

from nats_sinks.spool.config import SpoolDrainOrdering, SpoolDuplicatePolicy, SpoolSinkConfig
from nats_sinks.spool.record import (
    SPOOL_RECORD_SCHEMA,
    SPOOL_RECORD_VERSION,
    SPOOL_WRAPPER_SCHEMA,
    SPOOL_WRAPPER_VERSION,
    build_plain_record,
    envelope_from_plain_record,
    priority_rank,
    spool_filename_for_envelope,
    unwrap_record,
    wrap_record,
)
from nats_sinks.spool.sink import SpoolReplayResult, SpoolSink, replay_spool_to_sink

__all__ = [
    "SPOOL_RECORD_SCHEMA",
    "SPOOL_RECORD_VERSION",
    "SPOOL_WRAPPER_SCHEMA",
    "SPOOL_WRAPPER_VERSION",
    "SpoolDrainOrdering",
    "SpoolDuplicatePolicy",
    "SpoolReplayResult",
    "SpoolSink",
    "SpoolSinkConfig",
    "build_plain_record",
    "envelope_from_plain_record",
    "priority_rank",
    "replay_spool_to_sink",
    "spool_filename_for_envelope",
    "unwrap_record",
    "wrap_record",
]
