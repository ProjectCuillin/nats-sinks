# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from nats_sinks import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.sinks.connectors import SinkConnector, load_entry_point_connectors
from nats_sinks.sinks.registry import SinkRegistry


class MemorySink:
    """Small local sink used to keep registry tests independent."""

    async def start(self) -> None:
        return None

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        self.messages = list(messages)

    async def stop(self) -> None:
        return None


def test_registry_creates_registered_sink() -> None:
    registry = SinkRegistry()
    registry.register("memory", lambda _config: MemorySink())

    assert isinstance(registry.create("memory", {}), MemorySink)
    assert registry.names() == ("memory",)


def test_registry_rejects_unknown_sink() -> None:
    registry = SinkRegistry()

    with pytest.raises(ConfigurationError, match="unknown sink type"):
        registry.create("missing", {})


def test_registry_exposes_connector_metadata() -> None:
    registry = SinkRegistry()
    connector = SinkConnector(
        name="memory",
        factory=lambda _config: MemorySink(),
        summary="Memory sink for tests.",
        production_ready=True,
        certification=("unit",),
    )

    registry.register_connector(connector)

    assert registry.connector("memory").public_record()["production_ready"] is True
    assert registry.connectors() == (connector,)


def test_registry_rejects_duplicate_connector_names() -> None:
    registry = SinkRegistry()
    connector = SinkConnector(name="memory", factory=lambda _config: MemorySink(), summary="One.")

    registry.register_connector(connector)

    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register_connector(connector)


def test_connector_descriptor_validates_name_and_api_version() -> None:
    with pytest.raises(ConfigurationError, match="connector names"):
        SinkConnector(name="../bad", factory=lambda _config: MemorySink(), summary="Bad.")

    with pytest.raises(ConfigurationError, match="unsupported connector API"):
        SinkConnector(
            name="memory",
            factory=lambda _config: MemorySink(),
            summary="Bad API.",
            api_version="999",
        )


@dataclass
class FakeEntryPoint:
    name: str
    loaded: object
    loaded_count: int = 0

    def load(self) -> object:
        self.loaded_count += 1
        return self.loaded


def test_entry_point_discovery_loads_only_allow_listed_connectors() -> None:
    allowed = FakeEntryPoint(
        name="memory",
        loaded=SinkConnector(
            name="memory",
            factory=lambda _config: MemorySink(),
            summary="Allowed memory sink.",
            production_ready=True,
        ),
    )
    untrusted = FakeEntryPoint(
        name="untrusted",
        loaded=SinkConnector(
            name="untrusted",
            factory=lambda _config: MemorySink(),
            summary="Should not be loaded.",
            production_ready=True,
        ),
    )

    connectors = load_entry_point_connectors(
        allowed_names=("memory",),
        entry_points_provider=lambda: (untrusted, allowed),
    )

    assert tuple(connector.name for connector in connectors) == ("memory",)
    assert allowed.loaded_count == 1
    assert untrusted.loaded_count == 0


def test_entry_point_discovery_rejects_uninstalled_allowed_connector() -> None:
    with pytest.raises(ConfigurationError, match="not installed"):
        load_entry_point_connectors(
            allowed_names=("missing",),
            entry_points_provider=tuple,
        )


def test_entry_point_discovery_rejects_non_descriptor() -> None:
    entry_point = FakeEntryPoint(name="memory", loaded=lambda _config: MemorySink())

    with pytest.raises(ConfigurationError, match="must return a SinkConnector"):
        load_entry_point_connectors(
            allowed_names=("memory",),
            entry_points_provider=lambda: (entry_point,),
        )


def test_entry_point_discovery_rejects_non_production_ready_by_default() -> None:
    entry_point = FakeEntryPoint(
        name="memory",
        loaded=SinkConnector(
            name="memory",
            factory=lambda _config: MemorySink(),
            summary="Experimental memory sink.",
            status="experimental",
            production_ready=False,
        ),
    )

    with pytest.raises(ConfigurationError, match="not marked production-ready"):
        load_entry_point_connectors(
            allowed_names=("memory",),
            entry_points_provider=lambda: (entry_point,),
        )

    connectors = load_entry_point_connectors(
        allowed_names=("memory",),
        require_production_ready=False,
        entry_points_provider=lambda: (entry_point,),
    )
    assert connectors[0].name == "memory"


def test_entry_point_discovery_rejects_name_mismatch_and_builtin_claim() -> None:
    mismatched = FakeEntryPoint(
        name="memory",
        loaded=SinkConnector(
            name="other",
            factory=lambda _config: MemorySink(),
            summary="Mismatched.",
            production_ready=True,
        ),
    )
    with pytest.raises(ConfigurationError, match="returned connector"):
        load_entry_point_connectors(
            allowed_names=("memory",),
            entry_points_provider=lambda: (mismatched,),
        )

    builtin_claim = FakeEntryPoint(
        name="memory",
        loaded=SinkConnector(
            name="memory",
            factory=lambda _config: MemorySink(),
            summary="External connector.",
            built_in=True,
            production_ready=True,
        ),
    )
    with pytest.raises(ConfigurationError, match="must not claim built-in"):
        load_entry_point_connectors(
            allowed_names=("memory",),
            entry_points_provider=lambda: (builtin_claim,),
        )
