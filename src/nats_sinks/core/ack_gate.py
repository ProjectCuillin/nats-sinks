# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Reusable ACK-gating helpers for fan-out delivery.

Fan-out delivery needs a shared rule for deciding when JetStream may be ACKed
after several child sinks have been selected. This module keeps that decision
small, explicit, and testable: required child sinks must complete successfully,
while optional child sinks get a bounded grace window and must not block ACK
forever.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nats_sinks.core.config import RouteTargetConfig

FanoutTargetStatus = Literal["committed", "failed", "timed_out"]


class FanoutAckGateError(RuntimeError):
    """Raised when required fan-out work cannot safely be ACKed."""


class FanoutRequiredSinkError(FanoutAckGateError):
    """Raised when a required selected sink fails before ACK is allowed."""

    def __init__(self, sink: str, cause: BaseException) -> None:
        super().__init__("required fan-out sink failed before ACK")
        self.sink = sink
        self.__cause__ = cause


@dataclass(frozen=True, slots=True)
class FanoutTargetResult:
    """Public, payload-free result for one selected sink operation."""

    sink: str
    required: bool
    status: FanoutTargetStatus
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class FanoutAckGateResult:
    """Outcome of applying the ACK gate to selected child sink operations."""

    required: tuple[FanoutTargetResult, ...]
    optional: tuple[FanoutTargetResult, ...]

    @property
    def required_committed(self) -> tuple[str, ...]:
        """Return required sinks that completed before ACK."""

        return tuple(result.sink for result in self.required if result.status == "committed")

    @property
    def optional_committed(self) -> tuple[str, ...]:
        """Return optional sinks that completed before the ACK gate released."""

        return tuple(result.sink for result in self.optional if result.status == "committed")

    @property
    def optional_failed(self) -> tuple[str, ...]:
        """Return optional sinks that failed without blocking required ACK."""

        return tuple(result.sink for result in self.optional if result.status == "failed")

    @property
    def optional_timed_out(self) -> tuple[str, ...]:
        """Return optional sinks that exceeded the bounded ACK grace window."""

        return tuple(result.sink for result in self.optional if result.status == "timed_out")


async def wait_for_fanout_ack_gate(
    operations: Mapping[str, Awaitable[object]],
    targets: Sequence[RouteTargetConfig],
    *,
    logger: logging.Logger | None = None,
) -> FanoutAckGateResult:
    """Wait until all required targets complete and optional targets are bounded.

    All selected operations are scheduled immediately so optional side copies get
    the same chance to make progress as required custody sinks.  ACK is allowed
    only after every required operation has succeeded.  Optional operations are
    then observed for their configured `minimum_wait_ms`, capped by
    `timeout_ms`, and cancelled if they are still pending. The returned result
    contains internal sink identifiers and outcome categories only; it never
    includes payloads, subjects, headers, classification values, labels,
    connection strings, file paths, or exception messages.
    """

    target_by_name = {target.sink: target for target in targets}
    missing = sorted(set(target_by_name) - set(operations))
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"fan-out ACK gate is missing operation(s) for target(s): {joined}")

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    tasks = {name: asyncio.ensure_future(operations[name]) for name in target_by_name}
    required_results: list[FanoutTargetResult] = []
    optional_results: list[FanoutTargetResult] = []

    try:
        for target in targets:
            if not target.required:
                continue
            task = tasks[target.sink]
            try:
                await task
            except Exception as exc:
                _cancel_pending(tasks.values())
                if logger is not None:
                    logger.warning(
                        "fan-out required sink failed before ACK",
                        extra={"required": True},
                    )
                raise FanoutRequiredSinkError(target.sink, exc) from exc
            required_results.append(
                FanoutTargetResult(sink=target.sink, required=True, status="committed")
            )

        for target in targets:
            if target.required:
                continue
            result = await _observe_optional_target(
                target=target,
                task=tasks[target.sink],
                started_at=started_at,
                logger=logger,
            )
            optional_results.append(result)
    finally:
        _cancel_pending(tasks.values())

    return FanoutAckGateResult(
        required=tuple(required_results),
        optional=tuple(optional_results),
    )


async def _observe_optional_target(
    *,
    target: RouteTargetConfig,
    task: asyncio.Future[object],
    started_at: float,
    logger: logging.Logger | None,
) -> FanoutTargetResult:
    """Observe one optional task without letting it block ACK indefinitely."""

    wait_ms = target.minimum_wait_ms
    timeout_ms = target.timeout_ms
    if wait_ms is None or timeout_ms is None:
        raise ValueError("optional fan-out targets must have resolved wait and timeout values")

    if not task.done():
        loop = asyncio.get_running_loop()
        elapsed_ms = int((loop.time() - started_at) * 1_000)
        remaining_timeout_ms = max(0, timeout_ms - elapsed_ms)
        bounded_wait_ms = min(wait_ms, remaining_timeout_ms)
        try:
            await asyncio.wait_for(task, timeout=bounded_wait_ms / 1_000)
        except TimeoutError:
            task.cancel()
            _log_optional_timeout(logger, target.sink)
            return FanoutTargetResult(
                sink=target.sink,
                required=False,
                status="timed_out",
            )

    try:
        await task
    except asyncio.CancelledError:
        _log_optional_timeout(logger, target.sink)
        return FanoutTargetResult(
            sink=target.sink,
            required=False,
            status="timed_out",
        )
    except Exception as exc:
        if logger is not None:
            logger.warning(
                "fan-out optional sink failed before ACK gate released",
                extra={"required": False, "error_type": type(exc).__name__},
            )
        return FanoutTargetResult(
            sink=target.sink,
            required=False,
            status="failed",
            error_type=type(exc).__name__,
        )
    return FanoutTargetResult(sink=target.sink, required=False, status="committed")


def _cancel_pending(tasks: Iterable[asyncio.Future[object]]) -> None:
    """Cancel pending child tasks so optional work cannot run unbounded."""

    for task in tasks:
        if not task.done():
            task.cancel()


def _log_optional_timeout(logger: logging.Logger | None, sink: str) -> None:
    """Log optional timeout categories without exposing payload or destination detail."""

    if logger is None:
        return
    _ = sink
    logger.warning(
        "fan-out optional sink did not complete before ACK gate released",
        extra={"required": False},
    )


__all__ = [
    "FanoutAckGateError",
    "FanoutAckGateResult",
    "FanoutRequiredSinkError",
    "FanoutTargetResult",
    "FanoutTargetStatus",
    "wait_for_fanout_ack_gate",
]
