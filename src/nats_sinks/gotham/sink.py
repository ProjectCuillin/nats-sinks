# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Experimental Palantir Gotham sink."""

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
from nats_sinks.gotham.client import (
    GothamObjectClient,
    GothamObjectWriteResult,
    HttpGothamObjectClient,
)
from nats_sinks.gotham.config import GothamSinkConfig
from nats_sinks.gotham.mapping import prepare_gotham_batch


class GothamSink:
    """Write NATS envelopes to Palantir Gotham RevDB objects."""

    def __init__(
        self,
        *,
        gotham_base_url: str,
        object_type: str,
        external_id_property_type: str,
        subject_property_type: str,
        payload_property_type: str,
        bearer_token_env: str | None = None,
        config: GothamSinkConfig | None = None,
        client: GothamObjectClient | None = None,
        **config_values: Any,
    ) -> None:
        if config is None:
            try:
                config = GothamSinkConfig.model_validate(
                    {
                        "type": "gotham",
                        "gotham_base_url": gotham_base_url,
                        "object_type": object_type,
                        "external_id_property_type": external_id_property_type,
                        "subject_property_type": subject_property_type,
                        "payload_property_type": payload_property_type,
                        "bearer_token_env": bearer_token_env,
                        **config_values,
                    }
                )
            except PydanticValidationError as exc:
                raise ConfigurationError(str(exc)) from exc
        self.config = config
        self._client = client

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> GothamSink:
        """Create a Gotham sink from raw JSON configuration."""

        try:
            config = GothamSinkConfig.model_validate(mapping)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(
            gotham_base_url=config.gotham_base_url,
            object_type=config.object_type,
            external_id_property_type=config.external_id_property_type,
            subject_property_type=config.subject_property_type,
            payload_property_type=config.payload_property_type,
            config=config,
        )

    async def start(self) -> None:
        """Prepare the Gotham client lazily."""

        if self._client is None:
            self._client = HttpGothamObjectClient(self.config)

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write a batch and return only after Gotham acceptance is clear."""

        if not messages:
            return
        client = self._client
        if client is None:
            client = HttpGothamObjectClient(self.config)
            self._client = client

        for offset in range(0, len(messages), self.config.batch_size):
            chunk = messages[offset : offset + self.config.batch_size]
            prepared = prepare_gotham_batch(chunk, config=self.config)
            try:
                result = await client.create_objects(
                    prepared.objects,
                    timeout_seconds=self.config.timeout_seconds,
                )
            except NatsSinksError:
                raise
            except Exception as exc:
                raise DestinationUnavailableError(
                    "Gotham object client failed before objects were accepted"
                ) from exc
            _validate_object_result(result, expected_objects=len(prepared.objects))

    async def stop(self) -> None:
        """Release resources held by the sink."""


def _validate_object_result(result: GothamObjectWriteResult, *, expected_objects: int) -> None:
    """Fail closed on rejected, partial, or ambiguous Gotham results."""

    if result.rejected_objects > 0:
        raise PermanentSinkError("Gotham object create rejected one or more objects")
    accepted_or_duplicate = result.accepted_objects + result.duplicate_objects
    if accepted_or_duplicate != expected_objects:
        raise DestinationUnavailableError("Gotham object response did not confirm every object")
