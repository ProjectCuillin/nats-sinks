# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.oracle_nosql import OracleNoSqlSink, OracleNoSqlSinkConfig


def _enabled() -> bool:
    return os.environ.get("NATS_SINKS_ORACLE_NOSQL_INTEGRATION") == "1"


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_oracle_nosql_sink_writes_to_live_gated_backend() -> None:
    """Exercise a live Oracle NoSQL target only when explicitly enabled."""

    if not _enabled():
        pytest.skip("set NATS_SINKS_ORACLE_NOSQL_INTEGRATION=1 to run live Oracle NoSQL tests")

    pytest.importorskip("borneo")
    endpoint = os.environ.get("NATS_SINKS_ORACLE_NOSQL_ENDPOINT")
    table_name = os.environ.get("NATS_SINKS_ORACLE_NOSQL_TABLE")
    if not endpoint or not table_name:
        pytest.skip("set Oracle NoSQL endpoint and table env vars for live e2e")

    config = OracleNoSqlSinkConfig(
        endpoint=endpoint,
        deployment_mode=os.environ.get("NATS_SINKS_ORACLE_NOSQL_MODE", "kvstore"),
        auth_mode=os.environ.get("NATS_SINKS_ORACLE_NOSQL_AUTH_MODE") or None,
        table_name=table_name,
        namespace=os.environ.get("NATS_SINKS_ORACLE_NOSQL_NAMESPACE") or None,
        compartment_id=os.environ.get("NATS_SINKS_ORACLE_NOSQL_COMPARTMENT_ID") or None,
        cloudsim_tenant_id=os.environ.get("NATS_SINKS_ORACLE_NOSQL_CLOUDSIM_TENANT_ID")
        or "cloudsim",
        oci_config_file=os.environ.get("NATS_SINKS_ORACLE_NOSQL_OCI_CONFIG_FILE") or None,
        oci_profile=os.environ.get("NATS_SINKS_ORACLE_NOSQL_OCI_PROFILE", "DEFAULT"),
        oci_private_key_passphrase_env=os.environ.get(
            "NATS_SINKS_ORACLE_NOSQL_OCI_PRIVATE_KEY_PASSPHRASE_ENV"
        )
        or None,
        key_prefix="integration",
        auto_create=os.environ.get("NATS_SINKS_ORACLE_NOSQL_AUTO_CREATE") == "1",
    )
    sink = OracleNoSqlSink(config=config)
    envelope = NatsEnvelope(
        subject="integration.oracle_nosql.created",
        data=b'{"event_id":"NOSQL-E2E-1","status":"ok"}',
        headers={"Nats-Msg-Id": "nosql-e2e-1"},
        stream="NOSQL_E2E",
        consumer="oracle-nosql-e2e",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        message_id="nosql-e2e-1",
        redelivered=False,
        pending=0,
    )

    await sink.start()
    try:
        await sink.write_batch([envelope])
        await sink.write_batch([envelope])
    finally:
        await sink.stop()
