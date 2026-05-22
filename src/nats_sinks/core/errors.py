# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Framework exception hierarchy.

The runner needs to distinguish configuration problems, temporary sink
failures, permanent sink failures, serialization failures, DLQ failures, and
ACK failures.  This module centralizes those categories so sinks can translate
destination-specific driver errors into framework-level decisions.

The hierarchy is intentionally compact.  Temporary sink errors should leave a
message redeliverable or explicitly NAK it according to policy.  Permanent sink
errors may be moved to DLQ when configured.  ACK errors happen only after
durable success and therefore must be surfaced clearly because redelivery may
produce duplicates that idempotency must absorb.
"""


class NatsSinksError(Exception):
    """Base class for all nats-sinks errors."""


class ConfigurationError(NatsSinksError):
    """Configuration is invalid or incomplete."""


class SinkError(NatsSinksError):
    """A destination sink failed."""


class TemporarySinkError(SinkError):
    """A sink failure that may succeed on redelivery or retry."""


class PermanentSinkError(SinkError):
    """A sink failure that should not be retried without changing the input."""


class SerializationError(PermanentSinkError):
    """A message payload or encoded value could not be serialized or decoded."""


class ValidationError(PermanentSinkError):
    """A message or configuration value failed framework validation."""


class PolicyViolationError(ValidationError):
    """A normalized message failed the configured pre-sink policy gate."""


class DestinationUnavailableError(TemporarySinkError):
    """The destination is temporarily unavailable."""


class RetryExhaustedError(NatsSinksError):
    """Retry policy was exhausted."""


class DeadLetterError(NatsSinksError):
    """Publishing to a dead-letter destination failed."""


class AckError(NatsSinksError):
    """Acknowledging a JetStream message failed."""
