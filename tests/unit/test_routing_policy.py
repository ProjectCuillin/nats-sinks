# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for generic route-match policy validation and selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nats_sinks.core.config import (
    ConfigurationError,
    RoutePolicyRouteConfig,
    RoutingMatchPolicyConfig,
    load_config,
)
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.routing_policy import select_route_targets


def _envelope(
    *,
    subject: str = "mission.sensor.alpha",
    headers: dict[str, str] | None = None,
    priority: str | None = None,
    classification: str | None = None,
    labels: tuple[str, ...] = (),
) -> NatsEnvelope:
    return NatsEnvelope(
        subject=subject,
        data=b"{}",
        headers=headers or {},
        stream="MISSION",
        consumer="route-test",
        stream_sequence=1,
        consumer_sequence=1,
        timestamp=None,
        message_id="route-test-1",
        redelivered=False,
        pending=0,
        priority=priority,
        classification=classification,
        labels=labels,
    )


def _policy(*routes: RoutePolicyRouteConfig, mode: str = "first") -> RoutingMatchPolicyConfig:
    return RoutingMatchPolicyConfig(enabled=True, mode=mode, routes=routes)  # type: ignore[arg-type]


def _route(
    name: str,
    match: dict[str, object],
    targets: tuple[str, ...] = ("oracle_primary",),
) -> RoutePolicyRouteConfig:
    return RoutePolicyRouteConfig(name=name, match=match, targets=targets)


@pytest.mark.parametrize(
    ("match", "envelope"),
    [
        ({"subject": "mission.sensor.*"}, _envelope(subject="mission.sensor.alpha")),
        ({"priority": "urgent"}, _envelope(priority="urgent")),
        ({"classification": "NATO SECRET"}, _envelope(classification="NATO SECRET")),
        ({"labels_all": ["sensor", "audit"]}, _envelope(labels=("sensor", "audit", "edge"))),
        ({"labels_any": ["watch-floor", "edge"]}, _envelope(labels=("edge",))),
        ({"labels_none": ["training"]}, _envelope(labels=("sensor", "audit"))),
        (
            {"headers": [{"name": "Nats-Sinks-Route", "values": "mission-audit"}]},
            _envelope(headers={"nats-sinks-route": "mission-audit"}),
        ),
    ],
)
def test_route_match_policy_supports_each_match_operator(
    match: dict[str, object],
    envelope: NatsEnvelope,
) -> None:
    policy = _policy(_route("route_alpha", match))

    selection = select_route_targets(envelope, policy)

    assert selection.matched is True
    assert selection.matched_routes == ("route_alpha",)
    assert selection.targets == ("oracle_primary",)
    assert selection.action == "matched"


def test_route_match_policy_supports_combined_nato_examples() -> None:
    policy = _policy(
        _route(
            "nato_secret_sensor_audit",
            {
                "subject": "mission.sensor.>",
                "priority": "urgent",
                "classification": "NATO SECRET",
                "labels_all": ["sensor", "audit"],
                "labels_none": ["training"],
                "headers": [{"name": "Nats-Sinks-Route", "values": ["mission-audit"]}],
            },
            targets=("oracle_secret", "file_secret_audit"),
        ),
        _route(
            "nato_unclass_sensor_audit",
            {
                "subject": "mission.sensor.>",
                "priority": "urgent",
                "classification": "NATO UNCLASS",
                "labels_all": ["sensor", "audit"],
            },
            targets=("oracle_unclass",),
        ),
    )

    secret = select_route_targets(
        _envelope(
            headers={"Nats-Sinks-Route": "mission-audit"},
            priority="urgent",
            classification="NATO SECRET",
            labels=("sensor", "audit", "coalition"),
        ),
        policy,
    )
    unclass = select_route_targets(
        _envelope(
            priority="urgent",
            classification="NATO UNCLASS",
            labels=("sensor", "audit"),
        ),
        policy,
    )

    assert secret.matched_routes == ("nato_secret_sensor_audit",)
    assert secret.targets == ("oracle_secret", "file_secret_audit")
    assert unclass.matched_routes == ("nato_unclass_sensor_audit",)
    assert unclass.targets == ("oracle_unclass",)


def test_route_match_policy_all_mode_deduplicates_targets_in_route_order() -> None:
    policy = _policy(
        _route("subject_match", {"subject": "mission.>"}, targets=("oracle_primary",)),
        _route(
            "label_match",
            {"labels_any": ["audit"]},
            targets=("oracle_primary", "file_audit"),
        ),
        mode="all",
    )

    selection = select_route_targets(_envelope(labels=("audit",)), policy)

    assert selection.matched_routes == ("subject_match", "label_match")
    assert selection.targets == ("oracle_primary", "file_audit")


def test_route_match_policy_handles_missing_metadata_and_headers_without_crashing() -> None:
    policy = _policy(
        _route(
            "needs_metadata",
            {
                "priority": "urgent",
                "classification": "NATO SECRET",
                "labels_any": ["audit"],
                "headers": [{"name": "Nats-Sinks-Route", "values": "mission-audit"}],
            },
        )
    )

    selection = select_route_targets(_envelope(), policy)

    assert selection.matched is False
    assert selection.targets == ()
    assert selection.action == "reject"


def test_route_match_policy_handles_empty_and_multiple_labels() -> None:
    policy = _policy(_route("audit", {"labels_all": ["sensor", "audit"]}))

    assert select_route_targets(_envelope(labels=()), policy).matched is False
    assert (
        select_route_targets(_envelope(labels=("sensor", "audit", "audit")), policy).matched is True
    )


def test_route_match_policy_no_match_actions_are_explicit() -> None:
    envelope = _envelope(subject="other.subject")
    route = _route("mission", {"subject": "mission.>"})

    assert select_route_targets(envelope, _policy(route)).action == "reject"
    assert (
        select_route_targets(
            envelope,
            RoutingMatchPolicyConfig(
                enabled=True,
                no_match="ignore",
                routes=(route,),
            ),
        ).action
        == "ignore"
    )
    default_selection = select_route_targets(
        envelope,
        RoutingMatchPolicyConfig(
            enabled=True,
            no_match="default_route",
            default_targets=("file_default",),
            routes=(route,),
        ),
    )
    assert default_selection.action == "default_route"
    assert default_selection.targets == ("file_default",)


def test_route_match_policy_disabled_selects_no_targets() -> None:
    selection = select_route_targets(_envelope(), RoutingMatchPolicyConfig())

    assert selection.matched is False
    assert selection.targets == ()
    assert selection.action == "disabled"


def _write_config(tmp_path: Path, routing: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "MISSION",
                    "consumer": "route-test",
                    "subject": "mission.>",
                },
                "routing": routing,
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "events"),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_route_match_policy_load_config_accepts_documented_nato_example(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "enabled": True,
            "mode": "first",
            "no_match": "reject",
            "routes": [
                {
                    "name": "nato_secret_sensor_audit",
                    "match": {
                        "subject": "mission.sensor.>",
                        "priority": ["urgent"],
                        "classification": ["NATO SECRET"],
                        "labels_all": ["sensor", "audit"],
                        "headers": [{"name": "Nats-Sinks-Route", "values": ["mission-audit"]}],
                    },
                    "targets": ["oracle_secret", "file_secret_audit"],
                },
                {
                    "name": "nato_unclass_sensor_audit",
                    "match": {
                        "subject": "mission.sensor.>",
                        "priority": ["urgent"],
                        "classification": ["NATO UNCLASS"],
                        "labels_all": ["sensor", "audit"],
                    },
                    "targets": ["oracle_unclass"],
                },
            ],
        },
    )

    config = load_config(path, env_overrides=False)

    assert config.routing.enabled is True
    assert config.routing.routes[0].targets == ("oracle_secret", "file_secret_audit")
    assert config.routing.routes[1].targets == ("oracle_unclass",)


@pytest.mark.parametrize(
    ("routing", "message"),
    [
        (
            {
                "enabled": True,
                "mode": "many",
                "routes": [
                    {"name": "route_a", "match": {"subject": "mission.>"}, "targets": ["file_a"]}
                ],
            },
            "routing.mode",
        ),
        (
            {
                "enabled": True,
                "routes": [
                    {"name": "route_a", "match": {"subject": "mission..bad"}, "targets": ["file_a"]}
                ],
            },
            "invalid NATS subject pattern",
        ),
        (
            {
                "enabled": True,
                "routes": [
                    {
                        "name": "route_a",
                        "match": {"subject": "mission.>", "regex": ".*"},
                        "targets": ["file_a"],
                    }
                ],
            },
            "Extra inputs are not permitted",
        ),
        (
            {
                "enabled": True,
                "routes": [
                    {
                        "name": "route_a",
                        "match": {"priority": [f"p{i}" for i in range(65)]},
                        "targets": ["file_a"],
                    }
                ],
            },
            "supports at most 64 values",
        ),
        (
            {
                "enabled": True,
                "routes": [
                    {"name": "route_a", "match": {}, "targets": ["file_a"]},
                ],
            },
            "must contain at least one criterion",
        ),
        (
            {
                "enabled": True,
                "no_match": "default_route",
                "routes": [
                    {"name": "route_a", "match": {"subject": "mission.>"}, "targets": ["file_a"]}
                ],
            },
            "requires default_targets",
        ),
        (
            {
                "enabled": True,
                "routes": [
                    {
                        "name": "route_a",
                        "match": {
                            "headers": [{"name": "Authorization", "values": "secret-routing-value"}]
                        },
                        "targets": ["file_a"],
                    }
                ],
            },
            "must not match secret-bearing header",
        ),
    ],
)
def test_route_match_policy_load_config_rejects_malformed_variants(
    tmp_path: Path,
    routing: dict[str, object],
    message: str,
) -> None:
    path = _write_config(tmp_path, routing)

    with pytest.raises(ConfigurationError, match=message):
        load_config(path, env_overrides=False)
