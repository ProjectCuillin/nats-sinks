# SPDX-License-Identifier: Apache-2.0
"""Oracle-specific error helpers.

The Oracle driver may expose structured error codes or only formatted error
messages depending on the failure path and driver version.  This module keeps
code extraction in one place so the sink can translate duplicate-key,
configuration, and connectivity failures consistently.

Only small, deterministic helpers live here.  They do not import `oracledb`,
open connections, or inspect sensitive bind values.
"""

from __future__ import annotations

import re

_ORA_CODE_RE = re.compile(r"ORA-(\d{5})")


def oracle_error_code(error: BaseException) -> str | None:
    """Extract an ORA error code from a driver exception if available."""

    code = getattr(error, "code", None)
    if code is not None:
        return f"ORA-{int(code):05d}"
    match = _ORA_CODE_RE.search(str(error))
    if match:
        return f"ORA-{match.group(1)}"
    return None


def is_duplicate_error(error: BaseException) -> bool:
    """Return true for Oracle unique constraint violations."""

    return oracle_error_code(error) == "ORA-00001"
