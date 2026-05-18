# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.oracle.config import OracleTableRoute
from nats_sinks.oracle.routing import (
    matches_subject,
    resolve_table_for_subject,
    validate_subject_pattern,
)


def test_matches_subject_supports_nats_wildcards() -> None:
    assert matches_subject("orders.*", "orders.created")
    assert matches_subject("orders.>", "orders.created.eu")
    assert matches_subject("orders.created", "orders.created")
    assert not matches_subject("orders.*", "orders.created.eu")
    assert not matches_subject("orders.created", "orders.cancelled")


def test_invalid_subject_pattern_rejects_middle_full_wildcard() -> None:
    with pytest.raises(ConfigurationError, match="final token"):
        validate_subject_pattern("orders.>.created")


def test_invalid_subject_pattern_rejects_embedded_wildcards() -> None:
    with pytest.raises(ConfigurationError, match="invalid NATS subject route pattern"):
        validate_subject_pattern("orders.cre*ated")


def test_resolve_table_for_subject_uses_first_matching_route() -> None:
    table = resolve_table_for_subject(
        "orders.created.eu",
        default_table="NATS_SINK_EVENTS",
        routes=[
            OracleTableRoute(subject="orders.created.*", table="ORDER_CREATED_EVENTS"),
            OracleTableRoute(subject="orders.>", table="ORDER_EVENTS"),
        ],
    )

    assert table == "ORDER_CREATED_EVENTS"


def test_resolve_table_for_subject_falls_back_to_default_table() -> None:
    table = resolve_table_for_subject(
        "payments.created",
        default_table="NATS_SINK_EVENTS",
        routes=[OracleTableRoute(subject="orders.>", table="ORDER_EVENTS")],
    )

    assert table == "NATS_SINK_EVENTS"
