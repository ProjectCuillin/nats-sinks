# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Experimental Palantir Gotham sink package.

The connector currently targets Gotham RevDB object creation through a narrow
HTTP client boundary and local fake-client contract tests. Live certification
against an approved non-production Gotham environment remains a separate
release activity.
"""

from __future__ import annotations

from nats_sinks.gotham.client import (
    GothamObjectClient,
    GothamObjectWriteResult,
    HttpGothamObjectClient,
)
from nats_sinks.gotham.config import (
    GothamAuthMode,
    GothamExternalIdStrategy,
    GothamSinkConfig,
    GothamTarget,
    GothamValidationMode,
)
from nats_sinks.gotham.mapping import (
    GothamObjectWrite,
    GothamPreparedBatch,
    gotham_external_id,
    gotham_object_request_for_envelope,
    prepare_gotham_batch,
)
from nats_sinks.gotham.sink import GothamSink

__all__ = (
    "GothamAuthMode",
    "GothamExternalIdStrategy",
    "GothamObjectClient",
    "GothamObjectWrite",
    "GothamObjectWriteResult",
    "GothamPreparedBatch",
    "GothamSink",
    "GothamSinkConfig",
    "GothamTarget",
    "GothamValidationMode",
    "HttpGothamObjectClient",
    "gotham_external_id",
    "gotham_object_request_for_envelope",
    "prepare_gotham_batch",
)
