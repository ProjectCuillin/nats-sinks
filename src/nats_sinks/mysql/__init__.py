# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Public Oracle MySQL sink API.

The Oracle MySQL sink is shipped as a first-party sink beside the Oracle
Database, file, and spool sinks.  Importing this package is intentionally safe:
it does not import the optional Oracle MySQL Connector/Python driver or open a
network connection.  The driver is imported only when ``MySqlSink.start`` is
called, so projects can import public symbols without installing every sink
extra.
"""

from nats_sinks.mysql.config import (
    MySqlColumnMapping,
    MySqlIdempotencyConfig,
    MySqlSinkConfig,
    MySqlTableRoute,
    MySqlWriteMode,
)
from nats_sinks.mysql.sink import MySqlSink

__all__ = [
    "MySqlColumnMapping",
    "MySqlIdempotencyConfig",
    "MySqlSink",
    "MySqlSinkConfig",
    "MySqlTableRoute",
    "MySqlWriteMode",
]
