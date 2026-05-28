# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Experimental Palantir Foundry sink package.

The connector currently targets Foundry Streams push-based ingestion and uses a
small client protocol so local fake-client certification can happen without a
live Foundry environment.  Live certification remains a separate deployment
step before production recommendation.
"""

from nats_sinks.foundry.client import (
    FoundryStreamClient,
    FoundryStreamPushResult,
    HttpFoundryStreamClient,
)
from nats_sinks.foundry.config import (
    FoundryAuthMode,
    FoundryRecordKeyStrategy,
    FoundryRecordWrapper,
    FoundrySinkConfig,
    FoundryTarget,
)
from nats_sinks.foundry.mapping import (
    FoundryPreparedBatch,
    foundry_record_for_envelope,
    foundry_record_key,
    foundry_value_for_envelope,
    prepare_foundry_batch,
)
from nats_sinks.foundry.sink import FoundrySink

__all__ = [
    "FoundryAuthMode",
    "FoundryPreparedBatch",
    "FoundryRecordKeyStrategy",
    "FoundryRecordWrapper",
    "FoundrySink",
    "FoundrySinkConfig",
    "FoundryStreamClient",
    "FoundryStreamPushResult",
    "FoundryTarget",
    "HttpFoundryStreamClient",
    "foundry_record_for_envelope",
    "foundry_record_key",
    "foundry_value_for_envelope",
    "prepare_foundry_batch",
]
