# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generic route-match policy evaluation for normalized envelopes.

The routing policy is deliberately separate from sink execution.  It converts a
validated operator policy and one `NatsEnvelope` into logical sink target names,
but it does not open destinations, write messages, or ACK JetStream.  Keeping
selection separate from delivery lets the project evolve toward multi-sink
fan-out while preserving the core commit-then-acknowledge invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nats_sinks.core.config import (
    RouteMatchConfig,
    RoutePolicyRouteConfig,
    RouteTargetConfig,
    RoutingMatchPolicyConfig,
)
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.message_metadata import case_insensitive_header
from nats_sinks.core.subjects import matches_subject

NoMatchAction = Literal["disabled", "matched", "reject", "default_route", "ignore"]


@dataclass(frozen=True, slots=True)
class RouteSelection:
    """Result of evaluating a routing policy for one normalized envelope."""

    matched_routes: tuple[str, ...]
    targets: tuple[str, ...]
    action: NoMatchAction
    target_policies: tuple[RouteTargetConfig, ...] = ()

    @property
    def matched(self) -> bool:
        """Return whether at least one configured route matched the message."""

        return bool(self.matched_routes)


def _metadata_value_matches(configured: tuple[str, ...], actual: str | None) -> bool:
    if not configured:
        return True
    return actual is not None and actual in configured


def _labels_match(match: RouteMatchConfig, labels: tuple[str, ...]) -> bool:
    present = set(labels)
    if match.labels_all and not set(match.labels_all).issubset(present):
        return False
    if match.labels_any and not set(match.labels_any).intersection(present):
        return False
    if match.labels_none and set(match.labels_none).intersection(present):
        return False
    return True


def _headers_match(match: RouteMatchConfig, envelope: NatsEnvelope) -> bool:
    for header_match in match.headers:
        actual = case_insensitive_header(envelope.headers, header_match.name)
        if actual is None or actual not in header_match.values:
            return False
    return True


def route_matches_envelope(route: RoutePolicyRouteConfig, envelope: NatsEnvelope) -> bool:
    """Return whether one validated route matches a normalized envelope."""

    match = route.match
    if match.subject is not None and not matches_subject(match.subject, envelope.subject):
        return False
    if not _metadata_value_matches(match.priority, envelope.priority):
        return False
    if not _metadata_value_matches(match.classification, envelope.classification):
        return False
    if not _labels_match(match, envelope.labels):
        return False
    return _headers_match(match, envelope)


def _append_targets(
    existing_names: list[str],
    existing_policies: list[RouteTargetConfig],
    targets: tuple[RouteTargetConfig, ...],
) -> None:
    """Append target policies once while preserving policy order."""

    seen = set(existing_names)
    for target in targets:
        if target.sink in seen:
            continue
        existing_names.append(target.sink)
        existing_policies.append(target)
        seen.add(target.sink)


def select_route_targets(
    envelope: NatsEnvelope,
    policy: RoutingMatchPolicyConfig,
) -> RouteSelection:
    """Evaluate route policy and return the selected logical sink targets.

    `mode="first"` returns the first matching route.  `mode="all"` returns all
    matching routes and de-duplicates target names while preserving route order.
    No-match handling is explicit: `reject`, `ignore`, or `default_route`.
    Delivery code can use that action later without re-reading raw message
    headers or payloads.
    """

    if not policy.enabled:
        return RouteSelection(matched_routes=(), targets=(), action="disabled")

    matched_routes: list[str] = []
    selected_targets: list[str] = []
    selected_target_policies: list[RouteTargetConfig] = []
    for route in policy.routes:
        if not route_matches_envelope(route, envelope):
            continue
        matched_routes.append(route.name)
        _append_targets(selected_targets, selected_target_policies, route.targets)
        if policy.mode == "first":
            break

    if matched_routes:
        return RouteSelection(
            matched_routes=tuple(matched_routes),
            targets=tuple(selected_targets),
            action="matched",
            target_policies=tuple(selected_target_policies),
        )

    if policy.no_match == "default_route":
        return RouteSelection(
            matched_routes=(),
            targets=tuple(target.sink for target in policy.default_targets),
            action="default_route",
            target_policies=policy.default_targets,
        )
    return RouteSelection(matched_routes=(), targets=(), action=policy.no_match)


__all__ = [
    "RouteSelection",
    "route_matches_envelope",
    "select_route_targets",
]
