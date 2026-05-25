# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for offline JetStream stream-management planning helpers."""

from pathlib import Path

import pytest

from nats_sinks.core.config import AppConfig
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.stream_management import (
    StreamManagementOptions,
    build_stream_management_plan,
    validate_stream_name,
)


def _config(tmp_path: Path, *, extra_nats: dict[str, object] | None = None) -> AppConfig:
    """Create a minimal validated file-sink config without network access."""

    return AppConfig.model_validate(
        {
            "nats": {
                "url": "nats://localhost:4222",
                "stream": "ORDERS",
                "consumer": "orders-sink",
                "subject": "orders.*",
                **(extra_nats or {}),
            },
            "sink": {
                "type": "file",
                "directory": str(tmp_path / "events"),
                "fsync": False,
            },
        }
    )


def test_stream_management_plan_uses_runtime_config_without_connecting(tmp_path: Path) -> None:
    plan = build_stream_management_plan(_config(tmp_path))

    assert plan.stream == "ORDERS"
    assert plan.subjects == ("orders.*",)
    assert plan.durable_consumer == "orders-sink"
    assert plan.settings.retention == "limits"
    assert "$JS.API.CONSUMER.MSG.NEXT.ORDERS.orders-sink" in plan.runtime_permissions
    assert "$JS.API.STREAM.CREATE.ORDERS" in plan.administration_permissions
    assert "nats stream add ORDERS" in plan.nats_cli_example
    assert "orders.*" in plan.nats_cli_example
    assert any("separate administrative identity" in note for note in plan.notes)


def test_stream_management_plan_honors_multi_filter_consumer_subjects(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        extra_nats={"subject": "orders.>"},
    )
    config.consumer_management.filter_subjects = ("orders.created", "orders.updated")

    plan = build_stream_management_plan(config)

    assert plan.subjects == ("orders.created", "orders.updated")
    assert "orders.created,orders.updated" in plan.nats_cli_example


def test_stream_management_options_reject_invalid_values() -> None:
    with pytest.raises(ConfigurationError, match="retention"):
        StreamManagementOptions(retention="forever")

    with pytest.raises(ConfigurationError, match="replicas"):
        StreamManagementOptions(replicas=0)

    with pytest.raises(ConfigurationError, match="duplicate_window_seconds"):
        StreamManagementOptions(duplicate_window_seconds=0)


def test_stream_management_options_emit_review_warnings(tmp_path: Path) -> None:
    plan = build_stream_management_plan(
        _config(tmp_path),
        StreamManagementOptions(
            retention="workqueue",
            discard="new",
            storage="memory",
            replicas=1,
            duplicate_window_seconds=30,
        ),
    )

    rendered = "\n".join(plan.warnings)
    assert "workqueue retention" in rendered
    assert "discard=new" in rendered
    assert "memory storage" in rendered
    assert "replicas=1" in rendered
    assert "duplicate_window_seconds" in rendered


def test_validate_stream_name_rejects_ambiguous_names() -> None:
    assert validate_stream_name("ORDERS") == "ORDERS"
    for value in (" ORDERS", "ORDERS.*", "ORDERS/PRIVATE", "ORDERS.INTERNAL"):
        with pytest.raises(ConfigurationError):
            validate_stream_name(value)
