# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Lifecycle primitives.

Long-running sink processes need cooperative shutdown rather than abrupt task
cancellation.  This module contains small lifecycle helpers that can be reused
by runners, tests, or future supervisors without importing NATS or sink drivers.

The first release keeps lifecycle state deliberately minimal.  The active
runner stops fetching, lets in-flight processing reach a durable boundary, and
then stops the sink.  More advanced supervisors can build on this package
without weakening the commit-then-acknowledge invariant.
"""

from __future__ import annotations

import asyncio


class ShutdownController:
    """Tracks cooperative shutdown state."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal that long-running work should stop at the next safe point."""

        self._event.set()

    @property
    def requested(self) -> bool:
        """Return whether shutdown has been requested."""

        return self._event.is_set()

    async def wait(self) -> None:
        """Wait until another task requests shutdown."""

        await self._event.wait()
