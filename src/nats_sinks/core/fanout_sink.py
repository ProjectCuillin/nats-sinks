# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Production fan-out sink orchestration.

The fan-out sink is a core delivery adapter: it does not know how to write to
Oracle Database, Oracle MySQL, files, or any other destination itself. Instead,
it evaluates the validated route policy for each normalized envelope, groups
messages per selected child sink, starts every child write immediately, and
uses the shared ACK-gate helper to decide when the upstream JetStream batch can
be considered safely committed.

Fan-out is intentionally at-least-once, not an atomic distributed transaction.
If one required child commits and another required child fails, the fan-out sink
raises a temporary sink error so the runner leaves the original message
redeliverable. Child sinks must therefore keep their normal idempotency
controls enabled for production use.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nats_sinks.core.ack_gate import FanoutRequiredSinkError, wait_for_fanout_ack_gate
from nats_sinks.core.config import RouteTargetConfig, RoutingMatchPolicyConfig
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import ConfigurationError, PermanentSinkError, TemporarySinkError
from nats_sinks.core.fanout_observability import (
    record_fanout_ack_gate_result,
    record_fanout_required_failure,
    record_fanout_route_selection,
)
from nats_sinks.core.metrics import MetricsRecorder
from nats_sinks.core.routing_policy import RouteSelection, select_route_targets
from nats_sinks.sinks.base import FlushableSink, HealthCheckableSink, SchemaAwareSink, Sink

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SelectedTarget:
    """Resolved target policy plus the messages selected for that child sink."""

    policy: RouteTargetConfig
    messages: tuple[NatsEnvelope, ...]


class FanoutSink:
    """Route normalized envelopes into one or more configured child sinks."""

    def __init__(
        self,
        *,
        children: Mapping[str, Sink],
        routing: RoutingMatchPolicyConfig,
        metrics: MetricsRecorder | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if not children:
            raise ConfigurationError("fan-out sink requires at least one child sink")
        if not routing.enabled:
            raise ConfigurationError("fan-out sink requires routing.enabled true")
        target_names = routing.target_names()
        if not target_names:
            raise ConfigurationError("fan-out sink routing must select at least one target")
        missing = sorted(target for target in target_names if target not in children)
        if missing:
            joined = ", ".join(missing)
            raise ConfigurationError(f"fan-out routing references unknown child sink(s): {joined}")

        self._children = dict(children)
        self._routing = routing
        self._metrics = metrics
        self._logger = logger or LOGGER

    def set_metrics(self, metrics: MetricsRecorder | None) -> None:
        """Attach core metrics to fan-out and any children exposing metrics hooks."""

        self._metrics = metrics
        for child in self._children.values():
            set_metrics = getattr(child, "set_metrics", None)
            if callable(set_metrics):
                set_metrics(metrics)

    async def start(self) -> None:
        """Start child sinks in deterministic order."""

        started: list[Sink] = []
        try:
            for name in sorted(self._children):
                child = self._children[name]
                await child.start()
                started.append(child)
        except Exception:
            for child in reversed(started):
                await _stop_child_safely(child, logger=self._logger)
            raise

    async def stop(self) -> None:
        """Stop child sinks in reverse deterministic order."""

        errors: list[BaseException] = []
        for name in sorted(self._children, reverse=True):
            try:
                await self._children[name].stop()
            except Exception as exc:
                errors.append(exc)
                self._logger.exception(
                    "fan-out child sink stop failed",
                    extra={"error_type": type(exc).__name__},
                )
        if errors:
            raise TemporarySinkError("one or more fan-out child sinks failed to stop") from errors[
                0
            ]

    async def healthcheck(self) -> None:
        """Health-check every child sink that exposes the optional protocol."""

        for name in sorted(self._children):
            child = self._children[name]
            if isinstance(child, HealthCheckableSink):
                try:
                    await child.healthcheck()
                except Exception as exc:
                    raise TemporarySinkError("fan-out child sink health check failed") from exc

    async def ensure_schema(self) -> None:
        """Ask schema-aware child sinks to create or validate their destinations."""

        for child in self._children.values():
            if isinstance(child, SchemaAwareSink):
                await child.ensure_schema()

    async def flush(self) -> None:
        """Flush every child sink that exposes the optional protocol."""

        for child in self._children.values():
            if isinstance(child, FlushableSink):
                await child.flush()

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write selected message subsets through the fan-out ACK gate."""

        if not messages:
            return

        batch_started = time.perf_counter()
        selected = self._select_targets(messages)
        if not selected:
            return

        operations = {
            target.policy.sink: self._children[target.policy.sink].write_batch(target.messages)
            for target in selected
        }
        policies = tuple(target.policy for target in selected)
        ack_gate_started = time.perf_counter()
        try:
            result = await wait_for_fanout_ack_gate(
                operations,
                policies,
                logger=self._logger,
            )
        except FanoutRequiredSinkError as exc:
            elapsed = time.perf_counter()
            record_fanout_required_failure(
                self._metrics,
                ack_wait_seconds=elapsed - ack_gate_started,
                batch_seconds=elapsed - batch_started,
                logger=self._logger,
            )
            raise TemporarySinkError("fan-out required child sink failed before ACK") from exc

        elapsed = time.perf_counter()
        record_fanout_ack_gate_result(
            self._metrics,
            result,
            ack_wait_seconds=elapsed - ack_gate_started,
            batch_seconds=elapsed - batch_started,
            logger=self._logger,
        )

    def _select_targets(self, messages: Sequence[NatsEnvelope]) -> tuple[_SelectedTarget, ...]:
        """Evaluate routing once per message and group envelopes per child sink."""

        ordered_targets: list[str] = []
        policies: dict[str, RouteTargetConfig] = {}
        grouped_messages: dict[str, list[NatsEnvelope]] = {}

        for message in messages:
            selection = select_route_targets(message, self._routing)
            record_fanout_route_selection(self._metrics, selection, logger=self._logger)
            self._handle_no_route(selection)
            for target in selection.target_policies:
                if target.sink not in policies:
                    ordered_targets.append(target.sink)
                    policies[target.sink] = target
                    grouped_messages[target.sink] = []
                else:
                    policies[target.sink] = _merge_target_policy(policies[target.sink], target)
                grouped_messages[target.sink].append(message)

        return tuple(
            _SelectedTarget(policy=policies[target], messages=tuple(grouped_messages[target]))
            for target in ordered_targets
        )

    @staticmethod
    def _handle_no_route(selection: RouteSelection) -> None:
        """Apply explicit no-match behavior without reading payload data."""

        if selection.action == "reject":
            raise PermanentSinkError("fan-out routing selected no child sink target")
        if selection.action in {"disabled"}:
            raise ConfigurationError("fan-out routing is disabled")


def _merge_target_policy(left: RouteTargetConfig, right: RouteTargetConfig) -> RouteTargetConfig:
    """Resolve repeated target policies for a mixed-message batch.

    If any selected route marks the target as required, the merged policy is
    required. Optional wait settings only matter when every selected route keeps
    that target optional; in that case the largest configured waits are used so
    the batch does not ACK earlier than any selected message's policy allowed.
    """

    if left.sink != right.sink:
        raise ValueError("cannot merge route target policies for different sinks")
    if left.required or right.required:
        return RouteTargetConfig(sink=left.sink, required=True)

    minimum_wait_ms = max(left.minimum_wait_ms or 0, right.minimum_wait_ms or 0)
    timeout_ms = max(left.timeout_ms or 0, right.timeout_ms or 0)
    return RouteTargetConfig(
        sink=left.sink,
        required=False,
        minimum_wait_ms=minimum_wait_ms,
        timeout_ms=timeout_ms,
    )


async def _stop_child_safely(child: Sink, *, logger: logging.Logger) -> None:
    """Best-effort cleanup for children started before a later child failed."""

    try:
        await child.stop()
    except Exception:
        logger.exception("fan-out child sink cleanup failed after startup error")


__all__ = ["FanoutSink"]
