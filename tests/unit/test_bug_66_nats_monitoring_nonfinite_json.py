# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for issue #66 NATS monitoring JSON strictness."""

from __future__ import annotations

import math
import ssl

import pytest

from nats_sinks.observability import ObservabilityPolicy
from nats_sinks.observability.nats_monitoring import (
    NATS_MONITORING_SNAPSHOT_SCHEMA,
    NatsMonitoringError,
    collect_nats_monitoring_snapshot,
    load_nats_monitoring_snapshot,
    write_nats_monitoring_snapshot,
)


def _policy() -> ObservabilityPolicy:
    return ObservabilityPolicy(
        enabled=True,
        nats_server_monitoring={
            "enabled": True,
            "base_url": "https:" + "//" + "nats-monitoring.example.test",
            "allowed_endpoints": ["/jsz"],
            "allowed_fields": ["jetstream.stats.messages"],
            "timeout_seconds": 1.0,
            "max_response_bytes": 4096,
        },
    )


def test_bug_66_collection_rejects_nonstandard_json_constants() -> None:
    """Monitoring endpoint collection should reject NaN JSON constants."""

    def fetch(
        url: str,
        timeout_seconds: float,
        max_response_bytes: int,
        context: ssl.SSLContext | None,
    ) -> tuple[int, bytes]:
        _ = (url, timeout_seconds, max_response_bytes, context)
        return 200, b'{"jetstream":{"stats":{"messages":NaN}}}'

    with pytest.raises(NatsMonitoringError, match="valid JSON"):
        collect_nats_monitoring_snapshot(_policy(), fetch=fetch)


def test_bug_66_snapshot_loader_rejects_nonstandard_json_constants(tmp_path) -> None:
    """Stored monitoring snapshots should use standards-compliant JSON."""

    snapshot = tmp_path / "nats-monitoring.json"
    snapshot.write_text(
        (
            f'{{"schema":"{NATS_MONITORING_SNAPSHOT_SCHEMA}",'
            '"generated_at_epoch_seconds":NaN,"endpoints":[]}\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not valid JSON"):
        load_nats_monitoring_snapshot(snapshot)


def test_bug_66_snapshot_writer_rejects_nonfinite_values(tmp_path) -> None:
    """Monitoring snapshot writing should not emit NaN JSON values."""

    with pytest.raises(ValueError):
        write_nats_monitoring_snapshot(
            {
                "schema": NATS_MONITORING_SNAPSHOT_SCHEMA,
                "generated_at_epoch_seconds": math.nan,
                "endpoints": [],
            },
            tmp_path / "nats-monitoring.json",
        )
