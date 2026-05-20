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
from nats_sinks.core.metrics import MetricsRecorder, NoopMetrics
from nats_sinks.sinks.base import Sink

LOGGER = logging.getLogger(__name__)


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
        self.dead_letter = dead_letter or DeadLetterConfig()
        self.message_metadata = message_metadata or MessageMetadataConfig()
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

        options = dict(self.nats_options)
        options.setdefault("servers", [self.nats_url])
        self._nc = await nats.connect(**options)
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
                    raw_messages = await self._subscription.fetch(
                        self.delivery.batch_size,
                        timeout=timeout,
                    )
                except TimeoutError:
                    continue
                if raw_messages:
                    await self.process_raw_batch(raw_messages)
        finally:
            await self.stop()

    async def process_raw_batch(self, raw_messages: Sequence[Any]) -> None:  # noqa: PLR0911
        """Process a batch of raw NATS messages.

        ACK is sent only after sink.write_batch returns success. On permanent failures,
        the original messages are ACKed only after DLQ publication succeeds.
        """

        if not raw_messages:
            return

        try:
            envelopes = [
                envelope_from_nats_message(
                    raw_message,
                    message_metadata=self.message_metadata,
                )
                for raw_message in raw_messages
            ]
        except Exception as exc:
            await self._handle_temporary_failure(
                raw_messages,
                exc,
                context="message normalization failure",
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
                log_exception=True,
            )
            return
        self.metrics.increment("messages_received_total", len(envelopes))
        self.metrics.set_value("current_batch_size", float(len(envelopes)))

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
        self.metrics.observe("batch_write_seconds", elapsed)
        self.metrics.increment("batches_written_total")
        self.metrics.increment("messages_written_total", len(envelopes))
        await self._ack_all(raw_messages)
        self.metrics.increment("messages_acked_total", len(raw_messages))
        self.metrics.set_value("last_success_timestamp", time.time())

    async def _handle_temporary_failure(
        self,
        raw_messages: Sequence[Any],
        error: BaseException,
        *,
        context: str = "temporary sink failure",
        log_exception: bool = False,
    ) -> None:
        self.metrics.increment("messages_failed_total", len(raw_messages))
        self.metrics.increment("sink_write_errors_total")
        if log_exception:
            LOGGER.error(
                "%s; message batch will remain redeliverable",
                context,
                exc_info=(type(error), error, error.__traceback__),
            )
        else:
            LOGGER.warning("%s; message batch will remain redeliverable: %s", context, error)
        if self.delivery.temporary_failure_action == "nak":
            await self._nak_all(raw_messages, delay=self.delivery.retry_backoff_ms / 1000)

    async def _handle_permanent_failure(
        self,
        raw_messages: Sequence[Any],
        envelopes: Sequence[NatsEnvelope],
        error: PermanentSinkError,
    ) -> None:
        self.metrics.increment("messages_failed_total", len(raw_messages))
        self.metrics.increment("sink_write_errors_total")
        if not self.dead_letter.enabled:
            LOGGER.error(
                "permanent sink failure and DLQ disabled; message batch left unacked: %s", error
            )
            return

        await self._publish_dlq(envelopes, error)
        self.metrics.increment("messages_dlq_total", len(envelopes))
        await self._ack_all(raw_messages)
        self.metrics.increment("messages_acked_total", len(raw_messages))

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
            msg = "failed to publish permanent failure to DLQ; original message was not ACKed"
            raise DeadLetterError(msg) from exc

    async def _ack_all(self, raw_messages: Sequence[Any]) -> None:
        try:
            for raw_message in raw_messages:
                await _maybe_await(raw_message.ack())
        except Exception as exc:
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
                self.metrics.increment("messages_nacked_total")
            except Exception:
                LOGGER.exception(
                    "failed to NAK JetStream message; leaving it for ack timeout redelivery"
                )
