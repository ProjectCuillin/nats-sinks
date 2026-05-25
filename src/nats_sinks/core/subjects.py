# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""NATS subject pattern helpers shared by core and sink implementations.

NATS subjects are dot-separated names such as `orders.created`.  Subscription
and routing configuration can use two wildcard tokens: `*` matches exactly one
subject token, and `>` matches one or more remaining tokens when it appears as
the final token.  nats-sinks uses this small, predictable subset for features
that need deterministic local matching, including subject-specific encryption
rules and Oracle subject-to-table routing.
"""

from __future__ import annotations

from nats_sinks.core.errors import ConfigurationError

SINGLE_TOKEN_WILDCARD = chr(42)
TAIL_WILDCARD = chr(62)


def matches_subject(pattern: object, subject: object) -> bool:
    """Return whether a concrete NATS subject matches a wildcard pattern.

    The function is intentionally defensive and returns `False` for non-string
    inputs instead of raising.  Runtime paths call it for every message in a
    batch, so it avoids network calls, client objects, and destination-specific
    behavior.  Pattern validation remains separate so configuration errors can
    fail fast during `nats-sink validate`.
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
    """Validate a NATS subject pattern used by local routing configuration.

    The validator rejects ambiguous patterns, empty tokens, whitespace, and
    wildcard characters embedded inside literal tokens.  Keeping the accepted
    syntax small makes subject rules easy to review and prevents accidental
    broad matches in production configurations.
    """

    if not isinstance(pattern, str):
        raise ConfigurationError(f"invalid NATS subject pattern {pattern!r}")
    if not pattern or pattern.strip() != pattern:
        raise ConfigurationError(f"invalid NATS subject pattern {pattern!r}")
    tokens = pattern.split(".")
    if any(not token for token in tokens):
        raise ConfigurationError(f"invalid NATS subject pattern {pattern!r}")
    for index, token in enumerate(tokens):
        if token == TAIL_WILDCARD and index != len(tokens) - 1:
            raise ConfigurationError("NATS '>' wildcard is only valid as the final token")
        if TAIL_WILDCARD in token and token != TAIL_WILDCARD:
            raise ConfigurationError(f"invalid NATS subject pattern {pattern!r}")
        if SINGLE_TOKEN_WILDCARD in token and token != SINGLE_TOKEN_WILDCARD:
            raise ConfigurationError(f"invalid NATS subject pattern {pattern!r}")
        if any(character.isspace() for character in token):
            raise ConfigurationError(f"invalid NATS subject pattern {pattern!r}")
    return pattern


def subject_pattern_is_subset(candidate: object, allowed: object) -> bool:
    """Return whether every subject matched by `candidate` is allowed.

    This helper intentionally implements a small conservative containment check
    for the NATS wildcard grammar accepted by `validate_subject_pattern`.
    It is used at configuration boundaries where widening a subject filter
    would be more dangerous than rejecting a valid but hard-to-prove pattern.
    """

    try:
        candidate_pattern = validate_subject_pattern(candidate)
        allowed_pattern = validate_subject_pattern(allowed)
    except ConfigurationError:
        return False

    candidate_tokens = candidate_pattern.split(".")
    allowed_tokens = allowed_pattern.split(".")

    is_subset = True
    allowed_tail_matched = False
    for index, candidate_token in enumerate(candidate_tokens):
        if index >= len(allowed_tokens):
            is_subset = False
            break
        allowed_token = allowed_tokens[index]
        if allowed_token == TAIL_WILDCARD:
            allowed_tail_matched = True
            break
        if candidate_token == TAIL_WILDCARD:
            is_subset = allowed_token == TAIL_WILDCARD
            break
        if allowed_token == SINGLE_TOKEN_WILDCARD:
            continue
        if candidate_token == SINGLE_TOKEN_WILDCARD:
            is_subset = False
            break
        if candidate_token != allowed_token:
            is_subset = False
            break

    return is_subset and (allowed_tail_matched or len(candidate_tokens) == len(allowed_tokens))
