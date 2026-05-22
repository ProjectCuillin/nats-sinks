# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Configuration models for the local filesystem sink.

The file sink has no third-party runtime dependency, but it still needs strict
configuration validation because file paths are security-sensitive.  The sink
accepts a root output directory and writes only sanitized, deterministic paths
under that root.

The default mode writes one JSON document per message.  That design is slower
than appending to a single file, but it is easier to make idempotent and crash
safe: a redelivered message maps to the same final path, and the default
duplicate policy treats an existing file as successful prior processing.

Optional gzip compression is intentionally standard-library only.  It can save
space for JSON and text-heavy streams without adding a runtime dependency, and
it leaves the durable file-placement boundary unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nats_sinks.core.payload import PayloadStorageMode

FileWriteMode = Literal["one_file_per_message"]
FileFilenameStrategy = Literal["stream_sequence", "message_id", "payload_sha256"]
FileDuplicatePolicy = Literal["skip_existing", "overwrite", "fail"]
FileCompression = Literal["none", "gzip"]


class FileSinkConfig(BaseModel):
    """Validated configuration for `FileSink`.

    `skip_existing` is the recommended production duplicate policy.  It lets
    redelivery after a successful file commit behave as success without
    rewriting the previous file.  `overwrite` is available for controlled local
    workflows, but it updates the stored timestamp metadata on redelivery and is
    therefore not the safest default.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["file"] = "file"
    directory: Path
    mode: FileWriteMode = "one_file_per_message"
    filename_strategy: FileFilenameStrategy = "stream_sequence"
    duplicate_policy: FileDuplicatePolicy = "skip_existing"
    payload_mode: PayloadStorageMode = "json_or_envelope"
    extension: str = ".json"
    compression: FileCompression = "none"
    compression_level: int = Field(default=6, ge=1, le=9)
    include_metadata: bool = True
    partition_by_subject: bool = True
    create_directory: bool = True
    fsync: bool = True
    pretty: bool = False

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: Path) -> Path:
        """Reject empty path values before the sink touches the filesystem."""

        if str(value).strip() == "":
            raise ValueError("sink.directory must not be empty")
        return value

    @field_validator("extension")
    @classmethod
    def validate_extension(cls, value: str) -> str:
        """Keep extensions filename-only and predictable."""

        if not value:
            raise ValueError("sink.extension must not be empty")
        if not value.startswith("."):
            raise ValueError("sink.extension must start with '.'")
        if "/" in value or "\\" in value:
            raise ValueError("sink.extension must not contain path separators")
        if value in {".", ".."}:
            raise ValueError("sink.extension must include a suffix after '.'")
        return value

    @model_validator(mode="after")
    def default_gzip_extension(self) -> FileSinkConfig:
        """Use a compressed suffix when gzip is selected without an override."""

        if self.compression == "gzip" and "extension" not in self.model_fields_set:
            self.extension = ".json.gz"
        return self
