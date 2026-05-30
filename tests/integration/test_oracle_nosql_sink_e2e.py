# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.oracle_nosql import OracleNoSqlSink, OracleNoSqlSinkConfig
from nats_sinks.oracle_nosql.mapping import oracle_nosql_key_for_envelope
from nats_sinks.oracle_nosql.sink import _BorneoOracleNoSqlClient
from nats_sinks.testing.disconnected_spool_replay import (
    DisconnectedSpoolReplayOptions,
    run_disconnected_spool_replay_certification,
)


def _enabled() -> bool:
    return os.environ.get("NATS_SINKS_ORACLE_NOSQL_INTEGRATION") == "1"


def _disconnected_replay_enabled() -> bool:
    return os.environ.get("NATS_SINKS_ORACLE_NOSQL_DISCONNECTED_REPLAY") == "1"


pytestmark = pytest.mark.integration


def _integration_config(
    *,
    endpoint: str | None = None,
    table_name: str | None = None,
) -> OracleNoSqlSinkConfig:
    endpoint = endpoint or os.environ.get("NATS_SINKS_ORACLE_NOSQL_ENDPOINT")
    table_name = table_name or os.environ.get("NATS_SINKS_ORACLE_NOSQL_TABLE")
    if not endpoint or not table_name:
        pytest.skip("set Oracle NoSQL endpoint and table env vars for live e2e")

    return OracleNoSqlSinkConfig(
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
        request_timeout_seconds=10.0,
    )


def _borneo_client(config: OracleNoSqlSinkConfig) -> Any:
    return _BorneoOracleNoSqlClient.from_config(config)


def _get_row(config: OracleNoSqlSinkConfig, *, key: str) -> dict[str, Any] | None:
    client = _borneo_client(config)
    try:
        request = (
            client._borneo.GetRequest()
            .set_table_name(config.table_name)
            .set_key({config.key_field: key})
        )
        result = client._handle.get(request)
        value = result.get_value()
        return value if isinstance(value, dict) else None
    finally:
        close = getattr(client._handle, "close", None)
        if callable(close):
            close()


class OracleNoSqlDisconnectedReplayBackend:
    """Adapter used by the disconnected spool-and-replay certification."""

    name = "Oracle NoSQL Database"

    def __init__(self, *, config: OracleNoSqlSinkConfig) -> None:
        self.config = config

    def reachable_sink(self) -> OracleNoSqlSink:
        return OracleNoSqlSink(config=self.config)

    def unreachable_sink(self) -> OracleNoSqlSink:
        config = self.config.model_copy(update={"endpoint": "http://127.0.0.1:1"})
        return OracleNoSqlSink(config=config)

    async def assert_expected_records(self, messages: Sequence[NatsEnvelope]) -> None:
        missing: list[str] = []
        for message in messages:
            key = oracle_nosql_key_for_envelope(message, config=self.config)
            row = _get_row(self.config, key=key)
            if row is None:
                missing.append(message.idempotency_key())
        assert not missing, f"missing Oracle NoSQL Database records: {len(missing)}"


@pytest.mark.asyncio
async def test_oracle_nosql_sink_writes_to_live_gated_backend() -> None:
    """Exercise a live Oracle NoSQL target only when explicitly enabled."""

    if not _enabled():
        pytest.skip("set NATS_SINKS_ORACLE_NOSQL_INTEGRATION=1 to run live Oracle NoSQL tests")

    pytest.importorskip("borneo")
    config = _integration_config()
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


@pytest.mark.asyncio
async def test_oracle_nosql_sink_disconnected_spool_replay_certification(
    tmp_path: Path,
) -> None:
    """Certify Oracle NoSQL Database replay after local spool custody."""

    if not (_enabled() and _disconnected_replay_enabled()):
        pytest.skip(
            "set NATS_SINKS_ORACLE_NOSQL_INTEGRATION=1 and "
            "NATS_SINKS_ORACLE_NOSQL_DISCONNECTED_REPLAY=1 to run disconnected replay"
        )

    pytest.importorskip("borneo")
    table = os.environ.get("NATS_SINKS_ORACLE_NOSQL_TABLE")
    if not table:
        pytest.skip("NATS_SINKS_ORACLE_NOSQL_TABLE is required")
    config = _integration_config(table_name=table)
    stream = f"NOSQL_DISC_{uuid.uuid4().hex[:12].upper()}"

    report = await run_disconnected_spool_replay_certification(
        OracleNoSqlDisconnectedReplayBackend(config=config),
        spool_directory=tmp_path / "spool",
        options=DisconnectedSpoolReplayOptions(stream=stream),
    )

    assert report.backend == "Oracle NoSQL Database"
    assert report.expected_backend_records == 3003
    assert report.spool_remaining_records == 0
    assert report.outage_proved is True
