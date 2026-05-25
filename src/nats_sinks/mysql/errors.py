# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle MySQL driver error helpers.

Oracle MySQL Connector/Python usually exposes an ``errno`` attribute on driver
exceptions, but some failure paths produce only text.  Keeping code extraction
small and deterministic lets the sink translate connectivity, authentication,
duplicate, and schema failures without logging bind values or payload data.
"""

from __future__ import annotations

import re

_MYSQL_CODE_RE = re.compile(r"\b(\d{4})\b")

DUPLICATE_KEY_ERROR = 1062
ACCESS_DENIED_ERROR = 1045
UNKNOWN_DATABASE_ERROR = 1049
NO_SUCH_TABLE_ERROR = 1146
UNKNOWN_COLUMN_ERROR = 1054
DATA_TOO_LONG_ERROR = 1406
INVALID_JSON_TEXT_ERROR = 3140
SYNTAX_ERROR = 1064
SERVER_GONE_AWAY_ERROR = 2006
SERVER_LOST_ERROR = 2013
CONNECTION_REFUSED_ERROR = 2003
DEADLOCK_ERROR = 1213
LOCK_WAIT_TIMEOUT_ERROR = 1205


def mysql_error_code(error: BaseException) -> int | None:
    """Extract an Oracle MySQL numeric error code if one is available."""

    for attr in ("errno", "code"):
        value = getattr(error, attr, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    match = _MYSQL_CODE_RE.search(str(error))
    if match:
        return int(match.group(1))
    return None


def is_duplicate_error(error: BaseException) -> bool:
    """Return true for Oracle MySQL duplicate-key violations."""

    return mysql_error_code(error) == DUPLICATE_KEY_ERROR
