# SPDX-License-Identifier: Apache-2.0
"""Oracle sink public package.

The Oracle package exposes `OracleSink` and its validated configuration model.
Importing this package does not connect to Oracle and does not import the
optional `oracledb` driver immediately; the driver is loaded by `OracleSink`
when the sink starts.

Oracle is the first production sink and therefore establishes the reference
implementation for future destinations.  It commits before returning success,
uses idempotent modes for production workloads, validates SQL identifiers, and
maps Oracle driver errors into framework error categories.
"""

from nats_sinks.oracle.config import OracleSinkConfig
from nats_sinks.oracle.sink import OracleSink

__all__ = ["OracleSink", "OracleSinkConfig"]
