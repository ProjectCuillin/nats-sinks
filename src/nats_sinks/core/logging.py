# SPDX-License-Identifier: Apache-2.0
"""Logging setup helpers.

nats-sinks uses Python's standard logging package by default to keep dependency
surface small and integration predictable.  The CLI configures a concise
timestamped format; library users may ignore this helper and configure logging
with their own application framework.

Logging must remain safe by default.  Payloads, credentials, tokens, private
keys, and full connection strings should not be emitted unless a user
explicitly chooses a more verbose, controlled development mode.
"""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure standard library logging for CLI usage."""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
