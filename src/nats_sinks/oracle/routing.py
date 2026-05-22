# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""NATS subject routing helpers for Oracle table selection.

OracleSink can write different message subjects to different tables while the
core runner continues to consume from one JetStream consumer.  This module
implements NATS-style subject matching for route configuration.  It supports
literal tokens, `*` as a single-token wildcard, and `>` as the final token that
matches one or more remaining tokens.

Routing is intentionally deterministic: routes are evaluated in configuration
order and the first match wins.  If no route matches, OracleSink writes to its
default table.
"""

from __future__ import annotations

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.subjects import (
    matches_subject,
)
from nats_sinks.core.subjects import (
    validate_subject_pattern as validate_core_subject_pattern,
)
from nats_sinks.oracle.config import OracleTableRoute
from nats_sinks.oracle.sql import validate_identifier


def validate_subject_pattern(pattern: object) -> str:
    """Validate route syntax before a sink starts processing messages.

    This validation is intentionally strict.  It rejects empty tokens,
    whitespace, and wildcard characters embedded inside literal tokens.  That
    makes route behavior predictable and ensures `nats-sink validate` catches
    bad routing configuration before a service is started.
    """

    try:
        return validate_core_subject_pattern(pattern)
    except ConfigurationError as exc:
        if "final token" in str(exc):
            raise ConfigurationError(str(exc)) from exc
        raise ConfigurationError(f"invalid NATS subject route pattern {pattern!r}") from exc


def resolve_table_for_subject(
    subject: str,
    *,
    default_table: str,
    routes: list[OracleTableRoute],
) -> str:
    """Resolve the configured Oracle table for a message subject."""

    route = resolve_route_for_subject(subject, routes=routes)
    if route is not None:
        return validate_identifier(route.table)
    return validate_identifier(default_table)


def resolve_route_for_subject(
    subject: str,
    *,
    routes: list[OracleTableRoute],
) -> OracleTableRoute | None:
    """Return the first configured route matching a subject, if any."""

    for route in routes:
        pattern = validate_subject_pattern(route.subject)
        if matches_subject(pattern, subject):
            return route
    return None
