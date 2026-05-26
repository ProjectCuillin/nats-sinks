# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Certification tests for generic routing and fan-out behavior."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from nats_sinks.cli.main import app
from nats_sinks.core.ack_gate import FanoutRequiredSinkError
from nats_sinks.core.config import RoutingMatchPolicyConfig
from nats_sinks.testing import (
    FanoutAckProbe,
    FanoutCertificationCase,
    FanoutOperationPlan,
    certify_fanout_ack_order,
    certify_fanout_route_selection,
    fanout_certification_envelope,
    fanout_certification_policy,
)
from nats_sinks.testing.fanout_certification import FanoutCertificationAction


def _case(
    *,
    name: str,
    envelope_kwargs: dict[str, object] | None = None,
    policy: RoutingMatchPolicyConfig | None = None,
    expected_routes: tuple[str, ...],
    expected_targets: tuple[str, ...],
    expected_action: str = "matched",
) -> FanoutCertificationCase:
    return FanoutCertificationCase(
        name=name,
        envelope=fanout_certification_envelope(**(envelope_kwargs or {})),
        policy=policy or fanout_certification_policy(),
        expected_action=cast(FanoutCertificationAction, expected_action),
        expected_routes=expected_routes,
        expected_targets=expected_targets,
    )


def _write_config(tmp_path: Path, config: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _base_config(tmp_path: Path) -> dict[str, object]:
    return {
        "nats": {
            "url": "nats://localhost:4222",
            "stream": "MISSION",
            "consumer": "fanout-certification",
            "subject": "mission.>",
        },
        "sink": {
            "type": "file",
            "directory": str(tmp_path / "active"),
            "fsync": False,
        },
    }


def test_fanout_certification_selects_documented_secret_and_unclass_routes() -> None:
    secret = certify_fanout_route_selection(
        _case(
            name="secret",
            expected_routes=("nato_secret_sensor_audit",),
            expected_targets=("oracle_secret", "file_audit"),
        )
    )
    unclass = certify_fanout_route_selection(
        _case(
            name="unclass",
            envelope_kwargs={"classification": "NATO UNCLASS", "headers": {}},
            expected_routes=("nato_unclass_sensor_audit",),
            expected_targets=("oracle_unclass",),
        )
    )

    assert secret.target_policies[0].required is True
    assert secret.target_policies[1].required is False
    assert unclass.target_policies[0].required is True


def test_fanout_certification_matrix_covers_metadata_and_header_matching() -> None:
    policy = RoutingMatchPolicyConfig(
        enabled=True,
        routes=(
            {
                "name": "combined_match",
                "match": {
                    "subject": "mission.sensor.*",
                    "priority": ["urgent"],
                    "classification": ["NATO SECRET"],
                    "labels_all": ["sensor", "audit"],
                    "labels_any": ["edge", "gateway"],
                    "labels_none": ["training"],
                    "headers": [{"name": "Nats-Sinks-Route", "values": ["mission-audit"]}],
                },
                "targets": ["oracle_secret"],
            },
        ),
    )

    selection = certify_fanout_route_selection(
        _case(
            name="combined",
            policy=policy,
            envelope_kwargs={"labels": ("sensor", "audit", "edge")},
            expected_routes=("combined_match",),
            expected_targets=("oracle_secret",),
        )
    )

    assert selection.action == "matched"


def test_fanout_certification_no_route_policies_are_explicit() -> None:
    route = {
        "name": "known_subject",
        "match": {"subject": "mission.sensor.>"},
        "targets": ["oracle_secret"],
    }
    reject = RoutingMatchPolicyConfig(enabled=True, no_match="reject", routes=(route,))
    ignore = RoutingMatchPolicyConfig(enabled=True, no_match="ignore", routes=(route,))
    default = RoutingMatchPolicyConfig(
        enabled=True,
        no_match="default_route",
        default_targets=["file_default"],
        routes=(route,),
    )
    envelope_kwargs = {"subject": "mission.other.alpha", "headers": {}}

    assert (
        certify_fanout_route_selection(
            _case(
                name="reject",
                policy=reject,
                envelope_kwargs=envelope_kwargs,
                expected_action="reject",
                expected_routes=(),
                expected_targets=(),
            )
        ).action
        == "reject"
    )
    assert (
        certify_fanout_route_selection(
            _case(
                name="ignore",
                policy=ignore,
                envelope_kwargs=envelope_kwargs,
                expected_action="ignore",
                expected_routes=(),
                expected_targets=(),
            )
        ).action
        == "ignore"
    )
    assert certify_fanout_route_selection(
        _case(
            name="default",
            policy=default,
            envelope_kwargs=envelope_kwargs,
            expected_action="default_route",
            expected_routes=(),
            expected_targets=("file_default",),
        )
    ).targets == ("file_default",)


@pytest.mark.asyncio
async def test_fanout_certification_ack_waits_for_required_before_ack() -> None:
    probe = FanoutAckProbe()

    result = await certify_fanout_ack_order(
        _case(
            name="secret",
            expected_routes=("nato_secret_sensor_audit",),
            expected_targets=("oracle_secret", "file_audit"),
        ),
        (
            FanoutOperationPlan("oracle_secret", delay_seconds=0.01),
            FanoutOperationPlan("file_audit"),
        ),
        ack=probe.ack,
    )

    assert probe.called is True
    assert result.ack_gate is not None
    assert result.ack_gate.required_committed == ("oracle_secret",)
    assert result.ack_gate.optional_committed == ("file_audit",)
    assert result.events.index("oracle_secret:committed") < result.events.index("ack")


@pytest.mark.asyncio
async def test_fanout_certification_required_failure_after_partial_success_blocks_ack() -> None:
    policy = RoutingMatchPolicyConfig(
        enabled=True,
        routes=(
            {
                "name": "required_pair",
                "match": {"subject": "mission.sensor.>"},
                "targets": ["oracle_primary", "oracle_backup"],
            },
        ),
    )
    probe = FanoutAckProbe()
    events: list[str] = []

    with pytest.raises(FanoutRequiredSinkError, match="required fan-out sink failed") as exc_info:
        await certify_fanout_ack_order(
            _case(
                name="required-failure",
                policy=policy,
                expected_routes=("required_pair",),
                expected_targets=("oracle_primary", "oracle_backup"),
            ),
            (
                FanoutOperationPlan("oracle_primary"),
                FanoutOperationPlan("oracle_backup", outcome="fail"),
            ),
            ack=probe.ack,
            events=events,
        )
    assert exc_info.value.sink == "oracle_backup"

    assert probe.called is False
    assert "oracle_primary:committed" in events
    assert "ack" not in events


@pytest.mark.asyncio
async def test_fanout_certification_optional_timeout_is_bounded_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    probe = FanoutAckProbe()

    result = await certify_fanout_ack_order(
        _case(
            name="secret",
            expected_routes=("nato_secret_sensor_audit",),
            expected_targets=("oracle_secret", "file_audit"),
        ),
        (
            FanoutOperationPlan("oracle_secret"),
            FanoutOperationPlan("file_audit", outcome="hang"),
        ),
        ack=probe.ack,
        logger=logging.getLogger("nats_sinks.tests.fanout_certification"),
    )

    assert probe.called is True
    assert result.ack_gate is not None
    assert result.ack_gate.required_committed == ("oracle_secret",)
    assert result.ack_gate.optional_timed_out == ("file_audit",)
    assert "FANOUT-CERT-1" not in caplog.text


def test_fanout_certification_cli_validates_documented_named_sink_example() -> None:
    config = Path(__file__).resolve().parents[2] / "examples/named-multi-sink/config.json"

    result = CliRunner().invoke(app, ["validate", str(config)])

    assert result.exit_code == 0
    assert "nato_secret_sensor_audit" in result.output
    assert "nato_unclass_sensor_audit" in result.output
    assert "file_audit (optional, minimum_wait_ms=250, timeout_ms=1000)" in result.output


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda cfg: cfg.update({"routing": {"enabled": True, "routes": []}}),
            "routing.enabled requires at least one route",
        ),
        (
            lambda cfg: cfg.update(
                {
                    "sinks": {
                        "file_audit": {
                            "type": "file",
                            "directory": "audit",
                            "fsync": False,
                        }
                    },
                    "routing": {
                        "enabled": True,
                        "routes": [
                            {
                                "name": "missing_sink",
                                "match": {"subject": "mission.>"},
                                "targets": ["oracle_missing"],
                            }
                        ],
                    },
                }
            ),
            "unknown named sink",
        ),
        (
            lambda cfg: cfg.update(
                {
                    "routing": {
                        "enabled": True,
                        "routes": [
                            {
                                "name": "empty_match",
                                "match": {},
                                "targets": ["file_audit"],
                            }
                        ],
                    }
                }
            ),
            "must contain at least one criterion",
        ),
        (
            lambda cfg: cfg.update(
                {
                    "routing": {
                        "enabled": True,
                        "target_sink_types": {"file_audit": "file"},
                        "routes": [
                            {
                                "name": "bad_wait",
                                "match": {"subject": "mission.>"},
                                "targets": [
                                    {
                                        "sink": "file_audit",
                                        "required": False,
                                        "minimum_wait_ms": 2000,
                                        "timeout_ms": 1000,
                                    }
                                ],
                            }
                        ],
                    }
                }
            ),
            "timeout_ms must be at least minimum_wait_ms",
        ),
    ],
)
def test_fanout_certification_cli_rejects_invalid_routing_config(
    tmp_path: Path,
    mutator: Callable[[dict[str, object]], None],
    expected: str,
) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "file_audit": {
            "type": "file",
            "directory": str(tmp_path / "audit"),
            "fsync": False,
        }
    }
    mutator(config)

    result = CliRunner().invoke(app, ["validate", str(_write_config(tmp_path, config))])

    assert result.exit_code == 2
    assert expected in result.output


def test_fanout_certification_effective_config_redacts_secrets_but_preserves_targets(
    tmp_path: Path,
) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "oracle_secret": {
            "type": "oracle",
            "dsn": "tcps://adb.example.invalid/secret",
            "user": "app_secret",
            "password": "synthetic-password-marker",
            "table": "NATS_SECRET_EVENTS",
        }
    }
    config["routing"] = {
        "enabled": True,
        "routes": [
            {
                "name": "secret_route",
                "match": {"subject": "mission.>"},
                "targets": ["oracle_secret"],
            }
        ],
    }

    result = CliRunner().invoke(
        app,
        ["show-effective-config", str(_write_config(tmp_path, config))],
    )

    assert result.exit_code == 0
    assert "oracle_secret" in result.output
    assert "synthetic-password-marker" not in result.output
    assert '"password": "********"' in result.output
