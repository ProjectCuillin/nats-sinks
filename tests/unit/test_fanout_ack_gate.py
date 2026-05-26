# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for optional fan-out ACK-gating primitives."""

from __future__ import annotations

import asyncio
import logging

import pytest

from nats_sinks.core.ack_gate import FanoutRequiredSinkError, wait_for_fanout_ack_gate
from nats_sinks.core.config import RouteTargetConfig


async def _commit_after(delay_seconds: float = 0.0) -> str:
    await asyncio.sleep(delay_seconds)
    return "committed"


async def _fail_after(delay_seconds: float = 0.0) -> str:
    await asyncio.sleep(delay_seconds)
    raise RuntimeError("synthetic sink failure")


def _required(name: str) -> RouteTargetConfig:
    return RouteTargetConfig(sink=name)


def _optional(name: str, *, wait_ms: int = 25, timeout_ms: int = 100) -> RouteTargetConfig:
    return RouteTargetConfig(
        sink=name,
        required=False,
        minimum_wait_ms=wait_ms,
        timeout_ms=timeout_ms,
    )


@pytest.mark.asyncio
async def test_fanout_ack_gate_waits_for_required_and_records_optional_success() -> None:
    result = await wait_for_fanout_ack_gate(
        {
            "oracle_primary": _commit_after(0.01),
            "file_audit": _commit_after(0.0),
        },
        (_required("oracle_primary"), _optional("file_audit")),
    )

    assert result.required_committed == ("oracle_primary",)
    assert result.optional_committed == ("file_audit",)
    assert result.optional_failed == ()
    assert result.optional_timed_out == ()


@pytest.mark.asyncio
async def test_fanout_ack_gate_required_failure_blocks_ack() -> None:
    with pytest.raises(FanoutRequiredSinkError, match="required fan-out sink failed"):
        await wait_for_fanout_ack_gate(
            {
                "oracle_primary": _fail_after(0.0),
                "file_audit": _commit_after(0.1),
            },
            (_required("oracle_primary"), _optional("file_audit")),
        )


@pytest.mark.asyncio
async def test_fanout_ack_gate_optional_failure_does_not_block_required_ack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    result = await wait_for_fanout_ack_gate(
        {
            "oracle_primary": _commit_after(0.0),
            "file_audit": _fail_after(0.0),
        },
        (_required("oracle_primary"), _optional("file_audit")),
        logger=logging.getLogger("nats_sinks.tests.ack_gate"),
    )

    assert result.required_committed == ("oracle_primary",)
    assert result.optional_failed == ("file_audit",)
    assert "synthetic sink failure" not in caplog.text


@pytest.mark.asyncio
async def test_fanout_ack_gate_optional_wait_is_bounded() -> None:
    result = await asyncio.wait_for(
        wait_for_fanout_ack_gate(
            {
                "oracle_primary": _commit_after(0.0),
                "file_audit": _commit_after(10.0),
            },
            (_required("oracle_primary"), _optional("file_audit", wait_ms=10, timeout_ms=20)),
        ),
        timeout=0.5,
    )

    assert result.required_committed == ("oracle_primary",)
    assert result.optional_timed_out == ("file_audit",)


@pytest.mark.asyncio
async def test_fanout_ack_gate_rejects_missing_operation() -> None:
    committed = asyncio.get_running_loop().create_future()
    committed.set_result("committed")

    with pytest.raises(ValueError, match="missing operation"):
        await wait_for_fanout_ack_gate(
            {"oracle_primary": committed},
            (_required("oracle_primary"), _optional("file_audit")),
        )
