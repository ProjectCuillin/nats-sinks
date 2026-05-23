# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration models for the encrypted edge spool sink.

The spool sink is intentionally conservative because it changes the durable
boundary of a deployment.  When enabled as the active sink, JetStream messages
are acknowledged after the local spool file has been committed, not after a
remote database or object store has accepted the message.  Forwarding from the
spool to a final destination is a second at-least-once workflow.

Local disk is a sensitive custody location.  The configuration therefore fails
closed: record-level encryption must be enabled unless an operator explicitly
sets `allow_unencrypted` for local development or controlled testing.  The
spool is also bounded by record count and total byte size so disconnected edge
nodes cannot grow without limit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.config import EncryptionConfig
from nats_sinks.core.payload import PayloadStorageMode

SpoolDuplicatePolicy = Literal["skip_existing", "fail"]
SpoolDrainOrdering = Literal["priority", "fifo"]


class SpoolSinkConfig(BaseModel):
    """Validated configuration for `SpoolSink`.

    `skip_existing` is the production default because redelivery after a
    successful local spool commit should be treated as success.  A deterministic
    idempotency-key filename means the same message maps to the same spool
    record, allowing replay to preserve the original idempotency key.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["spool"] = "spool"
    directory: Path
    max_records: int = Field(default=100_000, ge=1, le=10_000_000)
    max_bytes: int = Field(default=10_737_418_240, ge=1_048_576, le=1_099_511_627_776)
    duplicate_policy: SpoolDuplicatePolicy = "skip_existing"
    payload_mode: PayloadStorageMode = "json_or_envelope"
    include_metadata: bool = True
    create_directory: bool = True
    fsync: bool = True
    pretty: bool = False
    drain_ordering: SpoolDrainOrdering = "priority"
    delete_after_replay: bool = True
    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)
    allow_unencrypted: bool = False

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: Path) -> Path:
        """Reject empty path values before any filesystem operation."""

        if str(value).strip() == "":
            raise ValueError("sink.directory must not be empty")
        return value

    @model_validator(mode="after")
    def validate_secure_defaults(self) -> SpoolSinkConfig:
        """Require encryption unless the operator explicitly accepts plaintext files."""

        if not self.encryption.enabled and not self.allow_unencrypted:
            raise ValueError(
                "spool sink requires sink.encryption.enabled=true unless "
                "sink.allow_unencrypted=true is set for local development"
            )
        return self
