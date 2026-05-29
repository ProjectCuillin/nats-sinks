# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Production fan-out sink tests.

These tests exercise the actual orchestration sink, not only the route selector
or ACK-gate primitives. They keep the first production implementation honest:
selected child sinks receive only their routed messages, optional targets cannot
block ACK forever, and required child failures still prevent the runner from
ACKing the original JetStream message.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nats_sinks.cli.main import app
from nats_sinks.core.config import DeliveryConfig, RoutePolicyRouteConfig, RoutingMatchPolicyConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError, PermanentSinkError, TemporarySinkError
from nats_sinks.core.fanout_sink import FanoutSink
from nats_sinks.core.metrics import InMemoryMetrics, MetricNames
from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.testing import fanout_certification_envelope, fanout_certification_policy


class RecordingChildSink:
    """Small child sink test double with optional failure or hang behavior."""

    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        fail: bool = False,
        hang: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.fail = fail
        self.hang = hang
        self.started = False
        self.stopped = False
        self.cancelled = False
        self.messages: list[NatsEnvelope] = []
        self.metrics = None

    def set_metrics(self, metrics: object) -> None:
        self.metrics = metrics

    async def start(self) -> None:
        self.events.append(f"{self.name}:start")
        self.started = True

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.events.append(f"{self.name}:write:{len(messages)}")
        self.messages.extend(messages)
        try:
            if self.hang:
                await asyncio.sleep(3600)
            if self.fail:
                self.events.append(f"{self.name}:failed")
                raise RuntimeError("synthetic child sink failure")
            self.events.append(f"{self.name}:commit")
        except asyncio.CancelledError:
            self.cancelled = True
            self.events.append(f"{self.name}:cancelled")
            raise

    async def stop(self) -> None:
        self.events.append(f"{self.name}:stop")
        self.stopped = True


def _fanout_sink(
    children: dict[str, RecordingChildSink],
    *,
    routing: RoutingMatchPolicyConfig | None = None,
    metrics: InMemoryMetrics | None = None,
) -> FanoutSink:
    effective_routing = routing or fanout_certification_policy()
    for target_name in effective_routing.target_names():
        children.setdefault(target_name, RecordingChildSink(target_name, []))
    return FanoutSink(
        children=children,
        routing=effective_routing,
        metrics=metrics,
    )


def test_fanout_sink_rejects_disabled_routing() -> None:
    with pytest.raises(ConfigurationError, match=r"routing\.enabled true"):
        FanoutSink(
            children={"file_audit": RecordingChildSink("file_audit", [])},
            routing=RoutingMatchPolicyConfig(),
        )


@pytest.mark.asyncio
async def test_fanout_sink_routes_one_message_to_multiple_child_sinks() -> None:
    events: list[str] = []
    oracle = RecordingChildSink("oracle_secret", events)
    audit = RecordingChildSink("file_audit", events)
    sink = _fanout_sink({"oracle_secret": oracle, "file_audit": audit})
    envelope = fanout_certification_envelope()

    await sink.write_batch([envelope])

    assert [message.subject for message in oracle.messages] == [envelope.subject]
    assert [message.subject for message in audit.messages] == [envelope.subject]
    assert "oracle_secret:write:1" in events
    assert "oracle_secret:commit" in events
    assert "file_audit:write:1" in events
    assert "file_audit:commit" in events


@pytest.mark.asyncio
async def test_fanout_sink_routes_mixed_batch_to_only_selected_children() -> None:
    events: list[str] = []
    oracle_secret = RecordingChildSink("oracle_secret", events)
    file_audit = RecordingChildSink("file_audit", events)
    oracle_unclass = RecordingChildSink("oracle_unclass", events)
    sink = _fanout_sink(
        {
            "oracle_secret": oracle_secret,
            "file_audit": file_audit,
            "oracle_unclass": oracle_unclass,
        }
    )
    secret = fanout_certification_envelope()
    unclass = fanout_certification_envelope(
        classification="NATO UNCLASS",
        headers={"Nats-Sinks-Route": "not-needed-for-unclass"},
    )

    await sink.write_batch([secret, unclass])

    assert oracle_secret.messages == [secret]
    assert file_audit.messages == [secret]
    assert oracle_unclass.messages == [unclass]


@pytest.mark.asyncio
async def test_fanout_sink_required_child_failure_blocks_success_after_partial_commit() -> None:
    events: list[str] = []
    oracle = RecordingChildSink("oracle_secret", events, fail=True)
    audit = RecordingChildSink("file_audit", events)
    sink = _fanout_sink({"oracle_secret": oracle, "file_audit": audit})

    with pytest.raises(TemporarySinkError, match="required child sink failed"):
        await sink.write_batch([fanout_certification_envelope()])

    assert "file_audit:commit" in events
    assert "oracle_secret:failed" in events


@pytest.mark.asyncio
async def test_fanout_sink_optional_child_timeout_does_not_block_success() -> None:
    events: list[str] = []
    oracle = RecordingChildSink("oracle_secret", events)
    audit = RecordingChildSink("file_audit", events, hang=True)
    metrics = InMemoryMetrics()
    sink = _fanout_sink({"oracle_secret": oracle, "file_audit": audit}, metrics=metrics)

    await asyncio.wait_for(sink.write_batch([fanout_certification_envelope()]), timeout=0.5)

    assert oracle.messages
    assert audit.cancelled is True
    assert metrics.counters[MetricNames.FANOUT_OPTIONAL_CHILD_TIMEOUT_TOTAL] == 1


@pytest.mark.asyncio
async def test_fanout_sink_no_match_rejects_or_ignores_explicitly() -> None:
    events: list[str] = []
    reject_sink = _fanout_sink({"oracle_secret": RecordingChildSink("oracle_secret", events)})

    with pytest.raises(PermanentSinkError, match="selected no child sink target"):
        await reject_sink.write_batch([fanout_certification_envelope(subject="mission.other")])

    ignore_policy = RoutingMatchPolicyConfig(
        enabled=True,
        mode="first",
        no_match="ignore",
        target_sink_types={"oracle_secret": "oracle"},
        routes=(
            RoutePolicyRouteConfig.model_validate(
                {
                    "name": "route_secret",
                    "match": {"subject": "mission.sensor.>"},
                    "targets": ["oracle_secret"],
                }
            ),
        ),
    )
    ignored_child = RecordingChildSink("oracle_secret", events)
    ignore_sink = _fanout_sink({"oracle_secret": ignored_child}, routing=ignore_policy)

    await ignore_sink.write_batch([fanout_certification_envelope(subject="mission.other")])

    assert ignored_child.messages == []


@dataclass
class FakeSequence:
    stream: int
    consumer: int


@dataclass
class FakeMetadata:
    stream: str = "MISSION"
    consumer: str = "fanout"
    sequence: FakeSequence = field(default_factory=lambda: FakeSequence(stream=1, consumer=1))
    num_delivered: int = 1
    num_pending: int = 0


class FakeMessage:
    def __init__(self, events: list[str]) -> None:
        self.subject = "mission.sensor.alpha"
        self.data = b'{"event_id":"FANOUT-ACK-1"}'
        self.headers = {
            "Nats-Sinks-Priority": "urgent",
            "Nats-Sinks-Classification": "NATO SECRET",
            "Nats-Sinks-Labels": "sensor;audit",
            "Nats-Sinks-Route": "mission-audit",
        }
        self.metadata = FakeMetadata()
        self.events = events
        self.acked = False
        self.nacked = False

    async def ack(self) -> None:
        self.events.append("ack")
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        del delay
        self.events.append("nak")
        self.nacked = True


@pytest.mark.asyncio
async def test_runner_does_not_ack_when_required_fanout_child_fails_after_partial_success() -> None:
    events: list[str] = []
    oracle = RecordingChildSink("oracle_secret", events, fail=True)
    audit = RecordingChildSink("file_audit", events)
    fanout = _fanout_sink({"oracle_secret": oracle, "file_audit": audit})
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="MISSION",
        consumer="fanout",
        subject="mission.>",
        sink=fanout,
        delivery=DeliveryConfig(temporary_failure_action="leave_unacked"),
    )
    message = FakeMessage(events)

    await runner.process_raw_batch([message])

    assert "file_audit:commit" in events
    assert "oracle_secret:failed" in events
    assert message.acked is False
    assert message.nacked is False


def _write_config(tmp_path: Path, config: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_cli_validate_accepts_inline_fanout_configuration(tmp_path: Path) -> None:
    config = {
        "nats": {
            "url": "nats://localhost:4222",
            "stream": "MISSION",
            "consumer": "fanout",
            "subject": "nl.mod.clas.>",
        },
        "sink": {
            "type": "fanout",
            "route_match_mode": "all",
            "sinks": {
                "oracle_secret": {
                    "type": "oracle",
                    "dsn": "oracle-primary-service",
                    "user": "app_user",
                    "password_env": "ORACLE_PRIMARY_PASSWORD",
                    "table": "SECRET_EVENTS",
                },
                "file_audit": {
                    "type": "file",
                    "directory": str(tmp_path / "audit"),
                    "fsync": False,
                },
                "oracle_unclass": {
                    "type": "oracle",
                    "dsn": "oracle-unclass-service",
                    "user": "app_user",
                    "password_env": "ORACLE_UNCLASS_PASSWORD",
                    "table": "UNCLASS_EVENTS",
                },
            },
            "routes": [
                {
                    "name": "secret-sensor-audit",
                    "match": {
                        "subject": "nl.mod.clas.>",
                        "priority": ["urgent"],
                        "classification": ["NATO SECRET"],
                        "labels_all": ["sensor", "audit"],
                    },
                    "targets": [
                        {"sink": "oracle_secret", "required": True},
                        {"sink": "file_audit", "required": False, "minimum_wait_ms": 500},
                    ],
                },
                {
                    "name": "unclass-sensor-oracle",
                    "match": {
                        "subject": "nl.mod.clas.>",
                        "priority": ["urgent"],
                        "classification": ["NATO UNCLASS"],
                        "labels_all": ["sensor", "audit"],
                    },
                    "targets": [{"sink": "oracle_unclass", "required": True}],
                },
            ],
        },
    }

    result = CliRunner().invoke(app, ["validate", str(_write_config(tmp_path, config))])

    assert result.exit_code == 0
    assert "Active sink: fanout" in result.output
    assert "oracle_secret (oracle)" in result.output
    assert "secret-sensor-audit" in result.output
    assert "file_audit (optional, minimum_wait_ms=500, timeout_ms=1000)" in result.output


def test_cli_validate_accepts_tracked_fanout_example() -> None:
    config = Path(__file__).resolve().parents[2] / "examples/fanout/config.json"

    result = CliRunner().invoke(app, ["validate", str(config)])

    assert result.exit_code == 0
    assert "Active sink: fanout" in result.output
    assert "secret-sensor-audit" in result.output
    assert "unclass-sensor-oracle" in result.output


def test_cli_validate_rejects_inline_fanout_unknown_target(tmp_path: Path) -> None:
    config = {
        "nats": {
            "url": "nats://localhost:4222",
            "stream": "MISSION",
            "consumer": "fanout",
            "subject": "mission.>",
        },
        "sink": {
            "type": "fanout",
            "route_match_mode": "all",
            "sinks": {
                "file_audit": {
                    "type": "file",
                    "directory": str(tmp_path / "audit"),
                    "fsync": False,
                }
            },
            "routes": [
                {
                    "name": "missing",
                    "match": {"subject": "mission.>"},
                    "targets": ["oracle_missing"],
                }
            ],
        },
    }

    result = CliRunner().invoke(app, ["validate", str(_write_config(tmp_path, config))])

    assert result.exit_code == 2
    assert "unknown named sink" in result.output
