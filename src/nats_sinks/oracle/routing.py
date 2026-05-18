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
from nats_sinks.oracle.config import OracleTableRoute
from nats_sinks.oracle.sql import validate_identifier

SINGLE_TOKEN_WILDCARD = chr(42)
TAIL_WILDCARD = chr(62)


def matches_subject(pattern: object, subject: object) -> bool:
    """Return whether a NATS subject matches a NATS wildcard pattern.

    The matcher implements the route subset that operators normally expect
    from NATS subscription syntax: literal tokens, `*` for exactly one token,
    and `>` as the final token for the remainder of the subject.  The function
    is deliberately pure and allocation-light so it can be used for every
    envelope in a batch without touching NATS or Oracle.
    """

    if not isinstance(pattern, str) or not isinstance(subject, str):
        return False

    pattern_tokens = pattern.split(".")
    subject_tokens = subject.split(".")

    for index, pattern_token in enumerate(pattern_tokens):
        if pattern_token == TAIL_WILDCARD:
            return index == len(pattern_tokens) - 1 and len(subject_tokens) > index
        if index >= len(subject_tokens):
            return False
        if pattern_token != SINGLE_TOKEN_WILDCARD and pattern_token != subject_tokens[index]:
            return False

    return len(subject_tokens) == len(pattern_tokens)


def validate_subject_pattern(pattern: object) -> str:
    """Validate route syntax before a sink starts processing messages.

    This validation is intentionally strict.  It rejects empty tokens,
    whitespace, and wildcard characters embedded inside literal tokens.  That
    makes route behavior predictable and ensures `nats-sink validate` catches
    bad routing configuration before a service is started.
    """

    if not isinstance(pattern, str):
        raise ConfigurationError(f"invalid NATS subject route pattern {pattern!r}")
    if not pattern or pattern.strip() != pattern:
        raise ConfigurationError(f"invalid NATS subject route pattern {pattern!r}")
    tokens = pattern.split(".")
    if any(not token for token in tokens):
        raise ConfigurationError(f"invalid NATS subject route pattern {pattern!r}")
    for index, token in enumerate(tokens):
        if token == TAIL_WILDCARD and index != len(tokens) - 1:
            raise ConfigurationError("NATS '>' wildcard is only valid as the final token")
        if TAIL_WILDCARD in token and token != TAIL_WILDCARD:
            raise ConfigurationError(f"invalid NATS subject route pattern {pattern!r}")
        if SINGLE_TOKEN_WILDCARD in token and token != SINGLE_TOKEN_WILDCARD:
            raise ConfigurationError(f"invalid NATS subject route pattern {pattern!r}")
        if any(character.isspace() for character in token):
            raise ConfigurationError(f"invalid NATS subject route pattern {pattern!r}")
    return pattern


def resolve_table_for_subject(
    subject: str,
    *,
    default_table: str,
    routes: list[OracleTableRoute],
) -> str:
    """Resolve the configured Oracle table for a message subject."""

    for route in routes:
        pattern = validate_subject_pattern(route.subject)
        if matches_subject(pattern, subject):
            return validate_identifier(route.table)
    return validate_identifier(default_table)
