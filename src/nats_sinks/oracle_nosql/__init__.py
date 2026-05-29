# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle NoSQL Database sink.

The package is safe to import without the optional Oracle NoSQL Python SDK
installed.  The SDK is imported lazily when ``OracleNoSqlSink.start`` opens a
real handle.
"""

from nats_sinks.oracle_nosql.config import (
    OracleNoSqlAuthMode,
    OracleNoSqlDeploymentMode,
    OracleNoSqlDuplicatePolicy,
    OracleNoSqlDurabilityMode,
    OracleNoSqlKeyStrategy,
    OracleNoSqlSinkConfig,
)
from nats_sinks.oracle_nosql.mapping import (
    ORACLE_NOSQL_EVENT_SCHEMA,
    ORACLE_NOSQL_EVENT_SCHEMA_VERSION,
    oracle_nosql_create_table_statement,
    oracle_nosql_key_for_envelope,
    oracle_nosql_row_for_envelope,
    oracle_nosql_value_for_envelope,
)
from nats_sinks.oracle_nosql.sink import (
    OracleNoSqlClient,
    OracleNoSqlClientFactory,
    OracleNoSqlSink,
)

__all__ = [
    "ORACLE_NOSQL_EVENT_SCHEMA",
    "ORACLE_NOSQL_EVENT_SCHEMA_VERSION",
    "OracleNoSqlAuthMode",
    "OracleNoSqlClient",
    "OracleNoSqlClientFactory",
    "OracleNoSqlDeploymentMode",
    "OracleNoSqlDuplicatePolicy",
    "OracleNoSqlDurabilityMode",
    "OracleNoSqlKeyStrategy",
    "OracleNoSqlSink",
    "OracleNoSqlSinkConfig",
    "oracle_nosql_create_table_statement",
    "oracle_nosql_key_for_envelope",
    "oracle_nosql_row_for_envelope",
    "oracle_nosql_value_for_envelope",
]
