# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Experimental Palantir Foundry sink.

``FoundrySink`` writes normalized envelopes to Foundry Streams push ingestion.
It is intentionally narrow and explicit: the core runner owns ACK decisions,
the sink owns only destination writes, and success is returned only after the
configured Foundry client reports that every record was accepted or known to be
an idempotent duplicate.

The sink is marked experimental until it is certified against an approved live
Foundry environment.  The local fake-client tests still exercise the same
client protocol so commit-then-ACK behavior can be verified without customer
credentials or tenant details.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    NatsSinksError,
    PermanentSinkError,
)
from nats_sinks.foundry.client import (
    FoundryStreamClient,
    FoundryStreamPushResult,
    HttpFoundryStreamClient,
)
from nats_sinks.foundry.config import FoundrySinkConfig
from nats_sinks.foundry.mapping import prepare_foundry_batch


class FoundrySink:
    """Write NATS envelopes to a Palantir Foundry stream."""

    def __init__(
        self,
        *,
        stream_push_url: str,
        bearer_token_env: str | None = None,
        config: FoundrySinkConfig | None = None,
        client: FoundryStreamClient | None = None,
        **config_values: Any,
    ) -> None:
        if config is None:
            try:
                config = FoundrySinkConfig.model_validate(
                    {
                        "type": "foundry",
                        "stream_push_url": stream_push_url,
                        "bearer_token_env": bearer_token_env,
                        **config_values,
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self._client = client

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> FoundrySink:
        """Create a Foundry sink from raw JSON configuration."""

        try:
            config = FoundrySinkConfig.model_validate(mapping)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(stream_push_url=config.stream_push_url, config=config)

    async def start(self) -> None:
        """Prepare the Foundry client.

        The default HTTP client resolves secrets lazily at write time so tests
        and dry configuration checks can validate structure without requiring
        live credentials.
        """

        if self._client is None:
            self._client = HttpFoundryStreamClient(self.config)

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write a batch and return only after Foundry acceptance is clear."""

        if not messages:
            return
        client = self._client
        if client is None:
            client = HttpFoundryStreamClient(self.config)
            self._client = client

        for offset in range(0, len(messages), self.config.batch_size):
            chunk = messages[offset : offset + self.config.batch_size]
            prepared = prepare_foundry_batch(chunk, config=self.config)
            try:
                result = await client.push_records(
                    prepared.records,
                    timeout_seconds=self.config.timeout_seconds,
                )
            except NatsSinksError:
                raise
            except Exception as exc:
                raise DestinationUnavailableError(
                    "Foundry stream client failed before records were accepted"
                ) from exc
            _validate_push_result(result, expected_records=len(prepared.records))

    async def stop(self) -> None:
        """Release resources.

        The default HTTP client is request-scoped and does not hold open sockets
        between writes.
        """


def _validate_push_result(result: FoundryStreamPushResult, *, expected_records: int) -> None:
    """Fail closed on rejected, partial, or ambiguous Foundry results."""

    if result.rejected_records > 0:
        raise PermanentSinkError("Foundry stream rejected one or more records")
    accepted_or_duplicate = result.accepted_records + result.duplicate_records
    if accepted_or_duplicate != expected_records:
        raise DestinationUnavailableError("Foundry stream response did not confirm every record")
