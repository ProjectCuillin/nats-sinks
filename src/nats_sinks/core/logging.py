# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
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

import copy
import logging
from collections.abc import Mapping
from typing import Any

from nats_sinks.core.errors import ConfigurationError

LOG_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_CONTROL_CHARACTER_REPLACEMENTS = {
    **{codepoint: f"\\x{codepoint:02x}" for codepoint in range(0x20)},
    0x7F: "\\x7f",
    ord("\n"): "\\n",
    ord("\r"): "\\r",
    ord("\t"): "\\t",
    0x1B: "\\x1b",
}


def normalize_log_level(level: str) -> str:
    """Return an allow-listed log level or fail closed.

    The CLI accepts a string because users provide the level through JSON,
    environment variables, or command-line options.  Treating unknown values as
    `INFO` would hide configuration mistakes, so invalid values raise a
    framework configuration error before the service starts.
    """

    normalized = level.strip().upper()
    if normalized not in LOG_LEVELS:
        allowed = ", ".join(LOG_LEVELS)
        raise ConfigurationError(f"logging.level must be one of: {allowed}")
    return normalized


def sanitize_log_text(value: str, *, max_length: int = 10_000) -> str:
    """Escape control characters before text reaches terminal or log sinks.

    Log output is an injection surface.  Untrusted NATS subjects, headers,
    file names, table names, and exception text can contain newlines or terminal
    escape sequences that make log records misleading.  The sanitizer keeps log
    records single-line and bounded without attempting to redact secrets; secret
    redaction is handled at the configuration and payload boundaries.
    """

    sanitized = value.translate(_CONTROL_CHARACTER_REPLACEMENTS)
    if len(sanitized) <= max_length:
        return sanitized
    return f"{sanitized[:max_length]}...<truncated>"


def _sanitize_log_value(value: Any) -> Any:
    """Recursively sanitize strings used as logging messages or arguments."""

    if isinstance(value, str):
        return sanitize_log_text(value)
    if isinstance(value, tuple):
        return tuple(_sanitize_log_value(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_log_value(item) for item in value]
    if isinstance(value, Mapping):
        return {_sanitize_log_value(key): _sanitize_log_value(item) for key, item in value.items()}
    return value


class SafeLogFormatter(logging.Formatter):
    """Formatter that keeps log records resistant to control-character injection."""

    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy.copy(record)
        safe_record.msg = _sanitize_log_value(safe_record.msg)
        safe_record.args = _sanitize_log_value(safe_record.args)
        return sanitize_log_text(super().format(safe_record))


def configure_logging(level: str = "INFO") -> None:
    """Configure standard library logging for CLI usage."""

    normalized = normalize_log_level(level)
    handler = logging.StreamHandler()
    handler.setFormatter(SafeLogFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.addHandler(handler)
    else:
        for existing_handler in root_logger.handlers:
            existing_handler.setFormatter(handler.formatter)
    root_logger.setLevel(LOG_LEVELS[normalized])
