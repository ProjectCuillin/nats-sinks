# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from nats_sinks import NatsEnvelope


def _load_oracle_integration_module() -> ModuleType:
    module_name = "_nats_sinks_oracle_integration_test_adapter"
    module_path = Path(__file__).resolve().parents[1] / "integration" / "test_oracle_sink.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Oracle integration test adapter.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


oracle_integration = _load_oracle_integration_module()


class _FakeOracleSink:
    def __init__(self) -> None:
        self._pool: object | None = None
        self.stopped = False

    async def start(self) -> None:
        self._pool = object()

    async def stop(self) -> None:
        self.stopped = True
        self._pool = None


def _message(sequence: int) -> NatsEnvelope:
    return NatsEnvelope(
        subject="disconnected.oracle",
        data=b"{}",
        headers={},
        stream="ORACLE_DISCONNECTED_VERIFY",
        consumer="unit",
        stream_sequence=sequence,
        consumer_sequence=sequence,
        timestamp=None,
        message_id=None,
        redelivered=False,
        pending=0,
    )


@pytest.mark.asyncio
async def test_oracle_disconnected_replay_verification_uses_non_destructive_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final verification must never call the drop-before-test setup helper."""

    messages = [_message(index) for index in range(1, 4)]
    fake_sink = _FakeOracleSink()

    async def forbidden_destructive_start(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("destructive integration setup helper must not be used")

    async def schema_check_noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(oracle_integration, "_oracle_sink", lambda *, table: fake_sink)
    monkeypatch.setattr(oracle_integration, "_start_sink_for_test", forbidden_destructive_start)
    monkeypatch.setattr(oracle_integration, "_assert_current_test_schema", schema_check_noop)
    monkeypatch.setattr(
        oracle_integration,
        "_count_rows",
        lambda pool, *, table, stream: len(messages),
    )
    monkeypatch.setattr(
        oracle_integration,
        "_count_distinct_rows",
        lambda pool, *, table, stream: len(messages),
    )

    backend = oracle_integration.OracleDisconnectedReplayBackend(table="SAFE_VERIFY")

    await backend.assert_expected_records(messages)

    assert fake_sink.stopped is True
