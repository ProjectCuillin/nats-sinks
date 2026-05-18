# SPDX-License-Identifier: Apache-2.0
"""Result models reserved for future sink extensions.

The first sink contract returns `None` from `write_batch` on success because
the only success signal the runner needs is that durable work completed.  This
module reserves a small structured result type for future APIs that may expose
diagnostic write counts, duplicate counts, or certification-test details.

The current runner does not depend on this result object.  Keeping it separate
lets future sinks add observability without changing the minimal production
contract too early.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BatchWriteResult:
    """Optional future result object for sinks that expose write details."""

    written: int
    duplicates: int = 0
