# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the optional NATS server monitoring observability connector."""

from __future__ import annotations

import json
import ssl
from pathlib import Path

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.observability import ObservabilityPolicy
from nats_sinks.observability.nats_monitoring import (
    NatsMonitoringError,
    build_nats_monitoring_url,
    collect_nats_monitoring_snapshot,
    extract_nats_monitoring_fields,
    load_nats_monitoring_snapshot,
    render_nats_monitoring_prometheus,
    write_nats_monitoring_snapshot,
)


def _policy(*, prometheus_enabled: bool = False) -> ObservabilityPolicy:
    return ObservabilityPolicy(
        enabled=True,
        namespace="mission_ops",
        nats_server_monitoring={
            "enabled": True,
            "base_url": "https://nats-monitoring.example.test",
            "allowed_endpoints": ["/healthz", "/jsz"],
            "allowed_fields": [
                "status",
                "server_id",
                "jetstream.stats.messages",
                "jetstream.stats.consumer_count",
            ],
            "timeout_seconds": 1.5,
            "max_response_bytes": 4096,
            "prometheus_enabled": prometheus_enabled,
        },
    )


def test_disabled_policy_refuses_collection() -> None:
    with pytest.raises(ConfigurationError, match="disabled"):
        collect_nats_monitoring_snapshot(ObservabilityPolicy())


def test_build_nats_monitoring_url_joins_validated_parts() -> None:
    assert build_nats_monitoring_url(_policy(), "/jsz") == (
        "https://nats-monitoring.example.test/jsz"
    )


def test_extract_fields_keeps_only_allowed_scalar_values() -> None:
    document = {
        "server_id": "server-a",
        "jetstream": {"stats": {"messages": 128, "nested": {"not": "exported"}}},
    }

    fields = extract_nats_monitoring_fields(
        document,
        ["server_id", "jetstream.stats.messages", "jetstream.stats.nested", "missing"],
    )

    assert fields == {
        "server_id": "server-a",
        "jetstream.stats.messages": 128,
        "jetstream.stats.nested": None,
        "missing": None,
    }


def test_collect_snapshot_fetches_only_approved_endpoints() -> None:
    calls: list[tuple[str, float, int, ssl.SSLContext | None]] = []

    def fetch(
        url: str,
        timeout_seconds: float,
        max_response_bytes: int,
        context: ssl.SSLContext | None,
    ) -> tuple[int, bytes]:
        calls.append((url, timeout_seconds, max_response_bytes, context))
        if url.endswith("/healthz"):
            return 200, json.dumps({"status": "ok", "server_id": "server-a"}).encode()
        return 200, json.dumps(
            {
                "server_id": "server-a",
                "jetstream": {"stats": {"messages": 42, "consumer_count": 3}},
            }
        ).encode()

    snapshot = collect_nats_monitoring_snapshot(_policy(), fetch=fetch)

    assert [call[0] for call in calls] == [
        "https://nats-monitoring.example.test/healthz",
        "https://nats-monitoring.example.test/jsz",
    ]
    assert calls[0][1] == 1.5
    assert calls[0][2] == 4096
    assert snapshot["schema"] == "nats_sinks.observability.nats_monitoring.snapshot.v1"
    endpoints = snapshot["endpoints"]
    assert isinstance(endpoints, list)
    assert endpoints[0]["fields"]["status"] == "ok"
    assert endpoints[1]["fields"]["jetstream.stats.messages"] == 42


def test_collect_snapshot_rejects_malformed_json() -> None:
    def fetch(
        url: str,
        timeout_seconds: float,
        max_response_bytes: int,
        context: ssl.SSLContext | None,
    ) -> tuple[int, bytes]:
        _ = (url, timeout_seconds, max_response_bytes, context)
        return 200, b"{not-json"

    with pytest.raises(NatsMonitoringError, match="valid JSON"):
        collect_nats_monitoring_snapshot(_policy(), fetch=fetch)


def test_snapshot_round_trip_and_prometheus_render(tmp_path: Path) -> None:
    snapshot = {
        "schema": "nats_sinks.observability.nats_monitoring.snapshot.v1",
        "generated_at_epoch_seconds": 1_797_820_000.0,
        "endpoints": [
            {
                "endpoint": "/jsz",
                "status_code": 200,
                "fields": {
                    "server_id": "server-a",
                    "jetstream.stats.messages": 42,
                    "jetstream.stats.consumer_count": 3,
                },
            }
        ],
    }
    snapshot_file = tmp_path / "nats-monitoring.json"

    write_nats_monitoring_snapshot(snapshot, snapshot_file)
    loaded = load_nats_monitoring_snapshot(snapshot_file)
    rendered = render_nats_monitoring_prometheus(loaded, _policy(prometheus_enabled=True))

    assert loaded["schema"] == "nats_sinks.observability.nats_monitoring.snapshot.v1"
    assert "mission_ops_nats_monitoring_jsz_jetstream_stats_messages 42" in rendered
    assert "mission_ops_nats_monitoring_jsz_jetstream_stats_consumer_count 3" in rendered
    assert "server-a" not in rendered


def test_prometheus_render_is_disabled_by_default() -> None:
    rendered = render_nats_monitoring_prometheus(None, ObservabilityPolicy())

    assert "disabled by observability policy" in rendered
