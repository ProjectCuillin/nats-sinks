# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.logging import SafeLogFormatter, normalize_log_level, sanitize_log_text


def test_sanitize_log_text_escapes_control_characters() -> None:
    assert sanitize_log_text("subject\nnext\rline\t\x1b[31m") == (
        "subject\\nnext\\rline\\t\\x1b[31m"
    )


def test_sanitize_log_text_bounds_output() -> None:
    rendered = sanitize_log_text("a" * 12, max_length=5)

    assert rendered == "aaaaa...<truncated>"


def test_safe_log_formatter_sanitizes_message_and_arguments() -> None:
    formatter = SafeLogFormatter("%(levelname)s %(message)s")
    record = logging.LogRecord(
        name="nats_sinks.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="subject=%s",
        args=("orders.created\nforged=1",),
        exc_info=None,
    )

    assert formatter.format(record) == "INFO subject=orders.created\\nforged=1"


def test_normalize_log_level_uses_allow_list() -> None:
    assert normalize_log_level("warning") == "WARNING"

    with pytest.raises(ConfigurationError, match=r"logging\.level must be one of"):
        normalize_log_level("TRACE")
