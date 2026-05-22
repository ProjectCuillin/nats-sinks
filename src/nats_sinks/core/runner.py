# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""JetStream sink runner implementing commit-then-acknowledge semantics.

`JetStreamSinkRunner` is the heart of the framework.  It owns NATS connectivity,
pull-based consumption, message normalization, sink lifecycle, DLQ publication,
metrics hooks, and every ACK/NAK decision.  Sinks receive only immutable
`NatsEnvelope` instances and return success only after their destination work is
durably complete.

The central ordering is non-negotiable: receive, validate, write, commit, ACK.
If sink writing fails, the runner does not ACK.  If a permanent failure is sent
to DLQ, the runner ACKs the original message only after DLQ publication
succeeds.  If durable commit succeeds but ACK fails or the process crashes
before ACK, redelivery may occur and must be handled through idempotent sink
behavior.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from nats_sinks.core.config import (
    DeadLetterConfig,
    DeliveryConfig,
    EncryptionConfig,
    MessageMetadataConfig,
    MissionMetadataConfig,
)
from nats_sinks.core.consumer import envelope_from_nats_message
from nats_sinks.core.dlq import build_dead_letter_payload
from nats_sinks.core.encryption import PayloadEncryptor, PayloadTransformer
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    AckError,
    ConfigurationError,
    DeadLetterError,
    PermanentSinkError,
    SinkError,
    TemporarySinkError,
)
from nats_sinks.core.metrics import (
    MetricNames,
    MetricsRecorder,
    NoopMetrics,
    increment_metric,
    observe_metric,
    set_metric_value,
)
from nats_sinks.core.priority import order_by_priority_lanes
from nats_sinks.core.retry import RetryPolicy
from nats_sinks.sinks.base import Sink

LOGGER = logging.getLogger(__name__)

_NATS_CONNECTION_EVENT_METRICS = {
    "disconnected_cb": MetricNames.NATS_CONNECTION_DISCONNECTED_TOTAL,
    "reconnected_cb": MetricNames.NATS_CONNECTION_RECONNECTED_TOTAL,
    "closed_cb": MetricNames.NATS_CONNECTION_CLOSED_TOTAL,
    "discovered_server_cb": MetricNames.NATS_DISCOVERED_SERVERS_TOTAL,
    "error_cb": MetricNames.NATS_ASYNC_ERRORS_TOTAL,
}


async def _maybe_await(value: object) -> None:
    if inspect.isawaitable(value):
        await value


class JetStreamSinkRunner:
    """Pull messages from JetStream, write through a sink, then ACK last."""

    def __init__(
        self,
        *,
        nats_url: str,
        stream: str,
        consumer: str,
        subject: str,
        sink: Sink,
        durable: bool = True,
        delivery: DeliveryConfig | None = None,
        dead_letter: DeadLetterConfig | None = None,
        message_metadata: MessageMetadataConfig | None = None,
        mission_metadata: MissionMetadataConfig | None = None,
        encryption: EncryptionConfig | None = None,
        metrics: MetricsRecorder | None = None,
        nats_options: Mapping[str, Any] | None = None,
        jetstream: Any | None = None,
        nats_connection: Any | None = None,
        payload_encryptor: PayloadTransformer | None = None,
    ) -> None:
        self.nats_url = nats_url
        self.stream = stream
        self.consumer = consumer
        self.subject = subject
        self.sink = sink
        self.durable = durable
        self.delivery = delivery or DeliveryConfig()
        self.retry_policy = RetryPolicy(
            max_retries=self.delivery.max_retries,
            backoff_ms=self.delivery.retry_backoff_ms,
            max_backoff_ms=self.delivery.retry_backoff_max_ms,
            backoff_mode=self.delivery.retry_backoff_mode,
            backoff_multiplier=self.delivery.retry_backoff_multiplier,
            jitter=self.delivery.retry_jitter,
        )
        self.dead_letter = dead_letter or DeadLetterConfig()
        self.message_metadata = message_metadata or MessageMetadataConfig()
        self.mission_metadata = mission_metadata or MissionMetadataConfig()
        self.encryption = encryption or EncryptionConfig()
        self.metrics = metrics or NoopMetrics()
        self.nats_options = dict(nats_options or {})
        self._payload_encryptor = (
            payload_encryptor
            if payload_encryptor is not None
            else PayloadEncryptor.from_config(self.encryption)
        )
        self._js = jetstream
        self._nc = nats_connection
        self._subscription: Any | None = None
        self._stop_requested = False

        if self.delivery.ack_policy != "after_sink_commit":
            raise ConfigurationError("only ack_policy='after_sink_commit' is supported")
        if self.dead_letter.enabled and not self.dead_letter.subject:
            raise ConfigurationError("dead_letter.subject is required when DLQ is enabled")

    async def start(self) -> None:
        """Start sink and connect to NATS if a JetStream context was not injected."""

        await self.sink.start()
        if self._js is not None:
            return

        import nats  # noqa: PLC0415 - keep client import lazy for import-safe package usage.

        self._nc = await nats.connect(**self._nats_connect_options())
        self._js = self._nc.jetstream()

    async def stop(self) -> None:
        """Stop the runner and release resources."""

        self._stop_requested = True
        await self.sink.stop()
        if self._nc is not None:
            close = getattr(self._nc, "close", None)
            if close is not None:
                await _maybe_await(close())

    def request_stop(self) -> None:
        """Request cooperative shutdown."""

        self._stop_requested = True

    def _nats_connect_options(self) -> dict[str, Any]:
        """Build NATS connection options and attach connection-event metrics.

        Embedding applications may pass their own `nats-py` callbacks through
        `nats_options`.  The runner wraps, rather than replaces, those callbacks
        so operational metrics are captured without taking away application
        hooks.
        """

        options = dict(self.nats_options)
        options.setdefault("servers", [self.nats_url])
        self._install_nats_connection_event_callbacks(options)
        return options

    def _install_nats_connection_event_callbacks(self, options: dict[str, Any]) -> None:
        """Wrap NATS client connection callbacks with safe metrics recording."""

        for callback_name, metric_name in _NATS_CONNECTION_EVENT_METRICS.items():
            existing = options.get(callback_name)
            options[callback_name] = self._connection_event_callback(
                callback_name=callback_name,
                metric_name=metric_name,
                existing=existing,
            )

    def _record_connection_event_metric(self, metric_name: str) -> None:
        """Record connection-event metrics without destabilizing NATS callbacks."""

        try:
            increment_metric(self.metrics, metric_name)
        except Exception:
            LOGGER.exception("failed to record NATS connection event metric")

    def _connection_event_callback(
        self,
        *,
        callback_name: str,
        metric_name: str,
        existing: Any | None,
    ) -> Any:
        """Return a `nats-py` callback that records metrics and preserves hooks."""

        async def _callback(*args: object) -> None:
            self._record_connection_event_metric(metric_name)
            if callback_name == "error_cb":
                error = args[0] if args else "unknown"
                LOGGER.warning("NATS asynchronous connection error observed: %s", error)
            elif callback_name == "disconnected_cb":
                LOGGER.warning("NATS connection disconnected; reconnect policy is now in effect")
            elif callback_name == "reconnected_cb":
                LOGGER.info("NATS connection reconnected")
            elif callback_name == "closed_cb":
                LOGGER.info("NATS connection closed")
            elif callback_name == "discovered_server_cb":
                LOGGER.info("NATS client discovered an additional server")

            if existing is None:
                return
            try:
                await _maybe_await(existing(*args))
            except Exception:
                LOGGER.exception("user-provided NATS connection event callback failed")

        return _callback

    async def run(self) -> None:
        """Run the pull-consumer loop until stopped."""

        await self.start()
        try:
            if self._js is None:
                raise ConfigurationError("JetStream context is not available")
            self._subscription = await self._js.pull_subscribe(
                self.subject,
                durable=self.consumer if self.durable else None,
                stream=self.stream,
            )
            timeout = self.delivery.batch_timeout_ms / 1000
            while not self._stop_requested:
                try:
                    # `batch_size` is an upper bound, not a requirement to wait
                    # forever for a full batch.  The nats-py pull fetch returns
                    # a partial list when messages are available but the fetch
                    # expires before the requested count is reached.  Processing
                    # that partial list keeps low-volume streams from waiting
                    # indefinitely while still bounding peak batch size.
                    fetch_started = time.perf_counter()
                    raw_messages = await self._subscription.fetch(
                        self.delivery.batch_size,
                        timeout=timeout,
                    )
                    observe_metric(
                        self.metrics,
                        MetricNames.NATS_FETCH_SECONDS,
                        time.perf_counter() - fetch_started,
                    )
                except TimeoutError:
                    continue
                if raw_messages:
                    await self.process_raw_batch(raw_messages)
        finally:
            await self.stop()

    async def process_raw_batch(self, raw_messages: Sequence[Any]) -> None:  # noqa: PLR0911, PLR0915
        """Process a batch of raw NATS messages.

        ACK is sent only after sink.write_batch returns success. On permanent failures,
        the original messages are ACKed only after DLQ publication succeeds.
        """

        if not raw_messages:
            return

        increment_metric(self.metrics, MetricNames.MESSAGES_FETCHED_TOTAL, len(raw_messages))
        increment_metric(self.metrics, MetricNames.BATCHES_FETCHED_TOTAL)
        mapping_started = time.perf_counter()
        try:
            envelopes = [
                envelope_from_nats_message(
                    raw_message,
                    message_metadata=self.message_metadata,
                    mission_metadata=self.mission_metadata,
                )
                for raw_message in raw_messages
            ]
            observe_metric(
                self.metrics,
                MetricNames.MESSAGE_MAPPING_SECONDS,
                time.perf_counter() - mapping_started,
            )
        except PermanentSinkError as exc:
            observe_metric(
                self.metrics,
                MetricNames.MESSAGE_MAPPING_SECONDS,
                time.perf_counter() - mapping_started,
            )
            try:
                fallback_envelopes = [
                    envelope_from_nats_message(
                        raw_message,
                        message_metadata=self.message_metadata,
                    )
                    for raw_message in raw_messages
                ]
            except Exception as fallback_exc:
                await self._handle_temporary_failure(
                    raw_messages,
                    fallback_exc,
                    context="message normalization failure",
                    error_metric=MetricNames.MESSAGE_NORMALIZATION_ERRORS_TOTAL,
                    log_exception=True,
                )
                return
            await self._handle_permanent_failure(raw_messages, fallback_envelopes, exc)
            return
        except Exception as exc:
            observe_metric(
                self.metrics,
                MetricNames.MESSAGE_MAPPING_SECONDS,
                time.perf_counter() - mapping_started,
            )
            await self._handle_temporary_failure(
                raw_messages,
                exc,
                context="message normalization failure",
                error_metric=MetricNames.MESSAGE_NORMALIZATION_ERRORS_TOTAL,
                log_exception=True,
            )
            return
        try:
            if self._payload_encryptor is not None:
                # Payload encryption happens in the core immediately before
                # sink delivery.  Metadata remains unchanged, while `data`
                # becomes a JSON encryption envelope that every sink can store
                # without learning destination-specific crypto behavior.
                envelopes = self._payload_encryptor.encrypt_batch(envelopes)
        except Exception as exc:
            await self._handle_temporary_failure(
                raw_messages,
                exc,
                context="payload encryption failure",
                error_metric=MetricNames.PAYLOAD_ENCRYPTION_ERRORS_TOTAL,
                log_exception=True,
            )
            return
        try:
            # Priority-lane scheduling is intentionally placed after all core
            # normalization and transformation work, but before sink delivery.
            # The sink receives one ordered batch and the runner still ACKs only
            # after that complete batch returns durable success.
            envelopes = order_by_priority_lanes(
                envelopes,
                self.delivery.priority_lanes,
                metrics=self.metrics,
            )
        except PermanentSinkError as exc:
            await self._handle_permanent_failure(raw_messages, envelopes, exc)
            return
        increment_metric(self.metrics, MetricNames.MESSAGES_PREPARED_TOTAL, len(envelopes))
        set_metric_value(self.metrics, MetricNames.CURRENT_BATCH_MESSAGES, float(len(envelopes)))

        started = time.perf_counter()
        try:
            await self.sink.write_batch(envelopes)
        except PermanentSinkError as exc:
            await self._handle_permanent_failure(raw_messages, envelopes, exc)
            return
        except TemporarySinkError as exc:
            await self._handle_temporary_failure(raw_messages, exc)
            return
        except SinkError as exc:
            await self._handle_temporary_failure(raw_messages, exc)
            return
        except Exception as exc:
            await self._handle_temporary_failure(
                raw_messages,
                exc,
                context="unexpected sink failure",
                log_exception=True,
            )
            return

        elapsed = time.perf_counter() - started
        observe_metric(self.metrics, MetricNames.SINK_BATCH_WRITE_SECONDS, elapsed)
        increment_metric(self.metrics, MetricNames.SINK_BATCHES_WRITTEN_TOTAL)
        increment_metric(self.metrics, MetricNames.MESSAGES_WRITTEN_TOTAL, len(envelopes))
        ack_started = time.perf_counter()
        await self._ack_all(raw_messages)
        observe_metric(
            self.metrics,
            MetricNames.MESSAGE_ACK_SECONDS,
            time.perf_counter() - ack_started,
        )
        increment_metric(self.metrics, MetricNames.MESSAGES_ACKED_TOTAL, len(raw_messages))
        set_metric_value(self.metrics, MetricNames.LAST_SINK_SUCCESS_EPOCH_SECONDS, time.time())

    async def _handle_temporary_failure(
        self,
        raw_messages: Sequence[Any],
        error: BaseException,
        *,
        context: str = "temporary sink failure",
        error_metric: str | None = MetricNames.SINK_WRITE_ERRORS_TOTAL,
        log_exception: bool = False,
    ) -> None:
        attempt = self._delivery_attempt_for_batch(raw_messages)
        increment_metric(self.metrics, MetricNames.MESSAGES_FAILED_TOTAL, len(raw_messages))
        if error_metric is not None:
            increment_metric(self.metrics, error_metric, len(raw_messages))
        if not self.retry_policy.should_retry(attempt):
            LOGGER.error(
                "%s; active retry budget exhausted at delivery attempt %s "
                "with max_retries=%s; message batch left redeliverable for JetStream policy: %s",
                context,
                attempt,
                self.retry_policy.max_retries,
                error,
            )
            return

        retry_delay = self.retry_policy.backoff_seconds(attempt)
        observe_metric(self.metrics, MetricNames.RETRY_BACKOFF_DELAY_SECONDS, retry_delay)
        if log_exception:
            LOGGER.error(
                "%s; message batch will remain redeliverable; "
                "delivery_attempt=%s retry_delay_seconds=%.3f",
                context,
                attempt,
                retry_delay,
                exc_info=(type(error), error, error.__traceback__),
            )
        else:
            LOGGER.warning(
                "%s; message batch will remain redeliverable; "
                "delivery_attempt=%s retry_delay_seconds=%.3f: %s",
                context,
                attempt,
                retry_delay,
                error,
            )
        if self.delivery.temporary_failure_action == "nak":
            await self._nak_all(raw_messages, delay=retry_delay)

    @staticmethod
    def _delivery_attempt_for_batch(raw_messages: Sequence[Any]) -> int:
        """Return the highest one-based JetStream delivery attempt in a batch.

        `nats-py` exposes the delivery attempt as `msg.metadata.num_delivered`.
        Test doubles and older client versions may omit it, so the runner falls
        back to attempt `1`.  The highest value in the batch is used so a mixed
        redelivery batch does not receive an overly aggressive delay.
        """

        attempts = [
            JetStreamSinkRunner._delivery_attempt_for_message(raw_message)
            for raw_message in raw_messages
        ]
        return max(attempts, default=1)

    @staticmethod
    def _delivery_attempt_for_message(raw_message: Any) -> int:
        """Read one message delivery attempt without trusting external objects."""

        try:
            metadata = getattr(raw_message, "metadata", None)
            delivered = getattr(metadata, "num_delivered", None)
            if delivered is None:
                return 1
            attempt = int(delivered)
        except (TypeError, ValueError):
            return 1
        except Exception:
            return 1
        return max(attempt, 1)

    async def _handle_permanent_failure(
        self,
        raw_messages: Sequence[Any],
        envelopes: Sequence[NatsEnvelope],
        error: PermanentSinkError,
    ) -> None:
        increment_metric(self.metrics, MetricNames.MESSAGES_FAILED_TOTAL, len(raw_messages))
        increment_metric(self.metrics, MetricNames.SINK_WRITE_ERRORS_TOTAL, len(raw_messages))
        if not self.dead_letter.enabled:
            LOGGER.error(
                "permanent sink failure and DLQ disabled; message batch left unacked: %s", error
            )
            return

        await self._publish_dlq(envelopes, error)
        increment_metric(self.metrics, MetricNames.MESSAGES_DLQ_TOTAL, len(envelopes))
        ack_started = time.perf_counter()
        await self._ack_all(raw_messages)
        observe_metric(
            self.metrics,
            MetricNames.MESSAGE_ACK_SECONDS,
            time.perf_counter() - ack_started,
        )
        increment_metric(self.metrics, MetricNames.MESSAGES_ACKED_TOTAL, len(raw_messages))

    async def _publish_dlq(
        self,
        envelopes: Sequence[NatsEnvelope],
        error: PermanentSinkError,
    ) -> None:
        if self._js is None:
            raise DeadLetterError("cannot publish DLQ message without a JetStream context")
        if self.dead_letter.subject is None:
            raise DeadLetterError("dead_letter.subject is not configured")

        try:
            for envelope in envelopes:
                payload = build_dead_letter_payload(
                    envelope,
                    error,
                    include_payload=self.dead_letter.include_payload,
                    include_headers=self.dead_letter.include_headers,
                    include_error=self.dead_letter.include_error,
                )
                headers = {
                    "Nats-Sinks-Error-Type": type(error).__name__,
                    "Nats-Sinks-Original-Subject": envelope.subject,
                }
                await self._js.publish(self.dead_letter.subject, payload, headers=headers)
        except Exception as exc:
            increment_metric(self.metrics, MetricNames.DLQ_PUBLISH_ERRORS_TOTAL, len(envelopes))
            msg = "failed to publish permanent failure to DLQ; original message was not ACKed"
            raise DeadLetterError(msg) from exc

    async def _ack_all(self, raw_messages: Sequence[Any]) -> None:
        acknowledged = 0
        try:
            for raw_message in raw_messages:
                await _maybe_await(raw_message.ack())
                acknowledged += 1
        except Exception as exc:
            failed_or_unknown = max(len(raw_messages) - acknowledged, 1)
            increment_metric(self.metrics, MetricNames.ACK_ERRORS_TOTAL, failed_or_unknown)
            raise AckError("failed to ACK JetStream message after durable sink success") from exc

    async def _nak_all(self, raw_messages: Sequence[Any], *, delay: float) -> None:
        for raw_message in raw_messages:
            nak = getattr(raw_message, "nak", None)
            if nak is None:
                continue
            try:
                try:
                    await _maybe_await(nak(delay=delay))
                except TypeError:
                    await _maybe_await(nak())
                increment_metric(self.metrics, MetricNames.MESSAGES_NACKED_TOTAL)
            except Exception:
                LOGGER.exception(
                    "failed to NAK JetStream message; leaving it for ack timeout redelivery"
                )
