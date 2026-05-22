# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""JetStream durable consumer management and drift checks.

The sink runner uses pull consumers because they give the core runtime bounded
fetching, explicit acknowledgement, and predictable backpressure.  Consumer
configuration is therefore delivery-sensitive: changing `AckPolicy`,
`FilterSubject`, `MaxAckPending`, or headers-only behavior can change when
messages redeliver or which messages are seen by the sink.

This module keeps those checks outside the hot message-processing path.  It is
used during startup only, before the runner fetches any messages.  If an
existing durable consumer is incompatible with the requested configuration, the
operation fails closed with a configuration error.  That is safer than silently
running with unexpected delivery behavior.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from nats_sinks.core.errors import ConfigurationError
from nats_sinks.core.subjects import subject_pattern_is_subset

_DELIVER_POLICY_NAMES = {
    "all": "ALL",
    "last": "LAST",
    "new": "NEW",
    "last_per_subject": "LAST_PER_SUBJECT",
}
_REPLAY_POLICY_NAMES = {
    "instant": "INSTANT",
    "original": "ORIGINAL",
}
FLOAT_COMPARISON_TOLERANCE = 0.001
ConsumerManagementMode = Literal["bind_only", "create_if_missing", "reconcile"]
ConsumerDeliverPolicy = Literal["all", "last", "new", "last_per_subject"]
ConsumerReplayPolicy = Literal["instant", "original"]


class ConsumerManagementConfigProtocol(Protocol):
    """Runtime shape needed from the Pydantic consumer-management config."""

    mode: ConsumerManagementMode
    filter_subjects: tuple[str, ...]
    deliver_policy: ConsumerDeliverPolicy
    replay_policy: ConsumerReplayPolicy
    ack_wait_seconds: float | None
    max_deliver: int | None
    backoff_seconds: tuple[float, ...] | None
    max_ack_pending: int | None
    max_waiting: int | None
    headers_only: bool | None
    num_replicas: int | None
    memory_storage: bool | None
    metadata: dict[str, str]


@dataclass(frozen=True, slots=True)
class ConsumerDrift:
    """One incompatible server-side consumer setting detected at startup."""

    field: str
    expected: object
    actual: object


@dataclass(frozen=True, slots=True)
class ConsumerManagementResult:
    """Summary of consumer-management work performed during startup."""

    mode: str
    action: str
    drift: tuple[ConsumerDrift, ...] = ()


async def _maybe_await(value: object) -> object:
    """Await client calls when a fake or client version returns an awaitable."""

    if inspect.isawaitable(value):
        return await value
    return value


def _is_not_found_error(exc: BaseException) -> bool:
    """Return true for nats-py NotFoundError without importing it eagerly."""

    return type(exc).__name__ == "NotFoundError"


def _consumer_config_class() -> tuple[type[Any], Any, Any, Any]:
    """Load nats-py consumer API classes lazily for import-safe package use."""

    try:
        from nats.js import api  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - runtime dependency failure.
        raise ConfigurationError("nats-py is required for JetStream consumer management") from exc
    return api.ConsumerConfig, api.AckPolicy, api.DeliverPolicy, api.ReplayPolicy


def _enum_value(value: object) -> str | None:
    """Normalize enum-like client values to their string representation."""

    if value is None:
        return None
    rendered = getattr(value, "value", value)
    if isinstance(rendered, str):
        return rendered
    return str(rendered)


def _field(config: object, name: str) -> object:
    """Read a field from nats-py dataclasses, dictionaries, or test doubles."""

    if isinstance(config, dict):
        return config.get(name)
    try:
        return getattr(config, name)
    except Exception:
        return None


def _existing_config(info: object) -> object:
    """Extract a consumer config object from a ConsumerInfo-like response."""

    config = _field(info, "config")
    if config is None:
        raise ConfigurationError("JetStream consumer info did not include a configuration object")
    return config


def _float_equal(expected: float | None, actual: object) -> bool:
    """Compare optional float settings with tolerance for client round trips."""

    if expected is None:
        return True
    if actual is None:
        return False
    try:
        actual_float = float(cast(Any, actual))
    except (TypeError, ValueError):
        return False
    return abs(actual_float - expected) <= FLOAT_COMPARISON_TOLERANCE


def _float_sequence_equal(expected: tuple[float, ...] | None, actual: object) -> bool:
    """Compare optional server-side duration sequences with tolerance."""

    if expected is None:
        return True
    if not isinstance(actual, (list, tuple)) or len(actual) != len(expected):
        return False
    for expected_item, actual_item in zip(expected, actual, strict=True):
        try:
            actual_float = float(cast(Any, actual_item))
        except (TypeError, ValueError):
            return False
        if abs(actual_float - expected_item) > FLOAT_COMPARISON_TOLERANCE:
            return False
    return True


def _managed_filter_subjects(
    subject: str,
    config: ConsumerManagementConfigProtocol,
) -> tuple[str, ...]:
    """Return the server-side filter subjects that the runner should manage."""

    if not config.filter_subjects:
        return (subject,)
    for filter_subject in config.filter_subjects:
        if not subject_pattern_is_subset(filter_subject, subject):
            raise ConfigurationError(
                "consumer_management.filter_subjects must not be broader than nats.subject; "
                f"{filter_subject!r} is not contained by {subject!r}"
            )
    return config.filter_subjects


def _existing_filter_subjects(existing_config: object) -> tuple[str, ...]:
    """Normalize single and plural server-side filter fields for comparison."""

    filter_subjects = _field(existing_config, "filter_subjects")
    if isinstance(filter_subjects, (list, tuple)) and filter_subjects:
        return tuple(str(item) for item in filter_subjects)
    filter_subject = _field(existing_config, "filter_subject")
    if isinstance(filter_subject, str) and filter_subject:
        return (filter_subject,)
    return ()


def build_consumer_config(
    *,
    stream: str,
    durable_name: str,
    subject: str,
    config: ConsumerManagementConfigProtocol,
) -> object:
    """Build a nats-py ConsumerConfig for controlled durable pull consumers."""

    del stream
    consumer_config_class, ack_policy, deliver_policy, replay_policy = _consumer_config_class()
    filter_subjects = _managed_filter_subjects(subject, config)
    kwargs: dict[str, object] = {
        "name": durable_name,
        "durable_name": durable_name,
        "ack_policy": ack_policy.EXPLICIT,
        "deliver_policy": getattr(deliver_policy, _DELIVER_POLICY_NAMES[config.deliver_policy]),
        "replay_policy": getattr(replay_policy, _REPLAY_POLICY_NAMES[config.replay_policy]),
    }
    if len(filter_subjects) == 1:
        kwargs["filter_subject"] = filter_subjects[0]
    else:
        kwargs["filter_subjects"] = list(filter_subjects)
    if config.ack_wait_seconds is not None:
        kwargs["ack_wait"] = float(config.ack_wait_seconds)
    if config.max_deliver is not None:
        kwargs["max_deliver"] = config.max_deliver
    if config.backoff_seconds is not None:
        kwargs["backoff"] = list(config.backoff_seconds)
    if config.max_ack_pending is not None:
        kwargs["max_ack_pending"] = config.max_ack_pending
    if config.max_waiting is not None:
        kwargs["max_waiting"] = config.max_waiting
    if config.headers_only is not None:
        kwargs["headers_only"] = config.headers_only
    if config.num_replicas is not None:
        kwargs["num_replicas"] = config.num_replicas
    if config.memory_storage is not None:
        kwargs["mem_storage"] = config.memory_storage
    if config.metadata:
        kwargs["metadata"] = dict(config.metadata)
    return consumer_config_class(**kwargs)


def _append_drift(
    drift: list[ConsumerDrift],
    field: str,
    expected: object,
    actual: object,
) -> None:
    """Record one drift item using short, non-sensitive values."""

    drift.append(ConsumerDrift(field=field, expected=expected, actual=actual))


def _consumer_name_matches(existing: object, consumer: str) -> bool:
    """Check durable identity across nats-py versions and test doubles."""

    name = _field(existing, "name")
    durable_name = _field(existing, "durable_name")
    return consumer in {name, durable_name}


def _detect_identity_and_filter_drift(
    drift: list[ConsumerDrift],
    existing_config: object,
    *,
    durable_name: str,
    expected_filter_subjects: tuple[str, ...],
) -> None:
    """Append durable identity, pull shape, and filter-subject drift."""

    if not _consumer_name_matches(existing_config, durable_name):
        _append_drift(
            drift,
            "durable_name",
            durable_name,
            _field(existing_config, "durable_name") or _field(existing_config, "name"),
        )

    deliver_subject = _field(existing_config, "deliver_subject")
    if deliver_subject:
        _append_drift(drift, "deliver_subject", None, deliver_subject)

    actual_filter_subjects = _existing_filter_subjects(existing_config)
    if set(actual_filter_subjects) != set(expected_filter_subjects):
        _append_drift(
            drift,
            "filter_subjects",
            sorted(expected_filter_subjects),
            sorted(actual_filter_subjects),
        )


def _detect_required_policy_drift(
    drift: list[ConsumerDrift],
    existing_config: object,
    config: ConsumerManagementConfigProtocol,
) -> None:
    """Append drift for always-managed delivery policies."""

    ack_policy = _enum_value(_field(existing_config, "ack_policy"))
    if ack_policy != "explicit":
        _append_drift(drift, "ack_policy", "explicit", ack_policy)

    deliver_policy = _enum_value(_field(existing_config, "deliver_policy"))
    if deliver_policy != config.deliver_policy:
        _append_drift(drift, "deliver_policy", config.deliver_policy, deliver_policy)

    replay_policy = _enum_value(_field(existing_config, "replay_policy"))
    if replay_policy != config.replay_policy:
        _append_drift(drift, "replay_policy", config.replay_policy, replay_policy)


def _detect_optional_policy_drift(
    drift: list[ConsumerDrift],
    existing_config: object,
    config: ConsumerManagementConfigProtocol,
) -> None:
    """Append drift for explicitly configured optional policy fields."""

    existing_headers_only = _field(existing_config, "headers_only")
    if config.headers_only is not None and existing_headers_only != config.headers_only:
        _append_drift(drift, "headers_only", config.headers_only, existing_headers_only)

    if not _float_equal(config.ack_wait_seconds, _field(existing_config, "ack_wait")):
        _append_drift(
            drift, "ack_wait", config.ack_wait_seconds, _field(existing_config, "ack_wait")
        )

    if not _float_sequence_equal(config.backoff_seconds, _field(existing_config, "backoff")):
        _append_drift(
            drift, "backoff", list(config.backoff_seconds or ()), _field(existing_config, "backoff")
        )

    for field_name in ("max_deliver", "max_ack_pending", "max_waiting"):
        expected = _field(config, field_name)
        if expected is None:
            continue
        actual = _field(existing_config, field_name)
        if actual != expected:
            _append_drift(drift, field_name, expected, actual)

    for config_field, consumer_field in (
        ("num_replicas", "num_replicas"),
        ("memory_storage", "mem_storage"),
    ):
        expected = _field(config, config_field)
        if expected is None:
            continue
        actual = _field(existing_config, consumer_field)
        if actual != expected:
            _append_drift(drift, consumer_field, expected, actual)

    if config.metadata:
        actual_metadata = _field(existing_config, "metadata") or {}
        if dict(cast(Any, actual_metadata)) != config.metadata:
            _append_drift(drift, "metadata", config.metadata, actual_metadata)


def detect_consumer_drift(
    existing: object,
    *,
    stream: str,
    durable_name: str,
    subject: str,
    config: ConsumerManagementConfigProtocol,
) -> tuple[ConsumerDrift, ...]:
    """Return incompatible consumer settings for an existing durable consumer."""

    del stream
    drift: list[ConsumerDrift] = []
    existing_config = _existing_config(existing)

    expected_filter_subjects = _managed_filter_subjects(subject, config)
    _detect_identity_and_filter_drift(
        drift,
        existing_config,
        durable_name=durable_name,
        expected_filter_subjects=expected_filter_subjects,
    )
    _detect_required_policy_drift(drift, existing_config, config)
    _detect_optional_policy_drift(drift, existing_config, config)

    return tuple(drift)


def _format_drift(drift: tuple[ConsumerDrift, ...]) -> str:
    """Render drift details without subjects outside the configured subject."""

    parts = [f"{item.field} expected={item.expected!r} actual={item.actual!r}" for item in drift]
    return "; ".join(parts)


async def _consumer_info(jetstream: object, stream: str, consumer: str) -> object | None:
    """Fetch consumer info, returning None when the consumer does not exist."""

    consumer_info = getattr(jetstream, "consumer_info", None)
    if not callable(consumer_info):
        raise ConfigurationError("JetStream context does not support consumer_info")
    try:
        return await _maybe_await(consumer_info(stream, consumer))
    except Exception as exc:
        if _is_not_found_error(exc):
            return None
        raise ConfigurationError("failed to read JetStream consumer configuration") from exc


async def _add_consumer(
    jetstream: object,
    *,
    stream: str,
    durable_name: str,
    subject: str,
    config: ConsumerManagementConfigProtocol,
) -> None:
    """Create or update a durable pull consumer through the JetStream API."""

    add_consumer = getattr(jetstream, "add_consumer", None)
    if not callable(add_consumer):
        raise ConfigurationError("JetStream context does not support add_consumer")
    consumer_config = build_consumer_config(
        stream=stream,
        durable_name=durable_name,
        subject=subject,
        config=config,
    )
    await _maybe_await(add_consumer(stream, config=consumer_config))


async def ensure_jetstream_consumer(
    jetstream: object,
    *,
    stream: str,
    durable_name: str,
    subject: str,
    durable: bool,
    config: ConsumerManagementConfigProtocol,
) -> ConsumerManagementResult:
    """Ensure a durable pull consumer exists and matches safe expectations.

    The helper is intentionally conservative.  It manages only durable pull
    consumers because ephemeral consumers cannot be reliably reconciled by name.
    Existing drift is reported as a configuration error before any fetch call
    can receive messages under unexpected delivery semantics.
    """

    if not durable:
        return ConsumerManagementResult(mode=config.mode, action="skipped_ephemeral")

    existing = await _consumer_info(jetstream, stream, durable_name)
    if existing is None:
        if config.mode == "bind_only":
            raise ConfigurationError(
                f"JetStream durable consumer {durable_name!r} does not exist in stream {stream!r}"
            )
        await _add_consumer(
            jetstream,
            stream=stream,
            durable_name=durable_name,
            subject=subject,
            config=config,
        )
        return ConsumerManagementResult(mode=config.mode, action="created")

    drift = detect_consumer_drift(
        existing,
        stream=stream,
        durable_name=durable_name,
        subject=subject,
        config=config,
    )
    if drift:
        raise ConfigurationError(
            "JetStream durable consumer configuration drift detected: " + _format_drift(drift)
        )

    if config.mode == "reconcile":
        await _add_consumer(
            jetstream,
            stream=stream,
            durable_name=durable_name,
            subject=subject,
            config=config,
        )
        return ConsumerManagementResult(mode=config.mode, action="reconciled")

    return ConsumerManagementResult(mode=config.mode, action="bound")
