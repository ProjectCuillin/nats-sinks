# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""NATS subject routing helpers for Oracle MySQL table selection."""

from __future__ import annotations

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.subjects import matches_subject
from nats_sinks.core.subjects import validate_subject_pattern as validate_core_subject_pattern
from nats_sinks.mysql.config import MySqlTableRoute
from nats_sinks.mysql.sql import validate_identifier


def validate_subject_pattern(pattern: object) -> str:
    """Validate route syntax before an Oracle MySQL sink starts."""

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
    routes: list[MySqlTableRoute],
) -> str:
    """Resolve the configured Oracle MySQL table for one message subject."""

    route = resolve_route_for_subject(subject, routes=routes)
    if route is not None:
        return validate_identifier(route.table)
    return validate_identifier(default_table)


def resolve_route_for_subject(
    subject: str,
    *,
    routes: list[MySqlTableRoute],
) -> MySqlTableRoute | None:
    """Return the first configured route matching a subject, if any."""

    for route in routes:
        pattern = validate_subject_pattern(route.subject)
        if matches_subject(pattern, subject):
            return route
    return None
