# SPDX-License-Identifier: Apache-2.0
"""Map NATS envelopes to filesystem paths and JSON documents.

The mapping layer is intentionally free of filesystem side effects.  It builds
safe relative paths and JSON-serializable records so tests can exercise naming,
metadata capture, payload handling, and idempotency decisions without writing
files.

All path components are derived through an allow-list sanitizer.  Subjects,
streams, and message IDs are external input, so the file sink never lets them
become raw path names.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import PermanentSinkError
from nats_sinks.file.config import FileSinkConfig

SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_COMPONENT_LENGTH = 120


def _digest(value: str | bytes) -> str:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def safe_path_component(value: object, *, fallback: str = "value") -> str:
    """Return a bounded filename component for untrusted values.

    The sanitizer preserves readable ASCII letters, numbers, dots, underscores,
    and hyphens.  Everything else becomes `_`.  Empty, current-directory, and
    parent-directory components are replaced with the fallback plus a digest so
    they cannot escape the configured sink directory.
    """

    rendered = str(value)
    cleaned = SAFE_COMPONENT_RE.sub("_", rendered).strip("._-")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = f"{fallback}-{_digest(rendered)[:16]}"
    if len(cleaned) > MAX_COMPONENT_LENGTH:
        cleaned = f"{cleaned[:80]}-{_digest(rendered)[:32]}"
    return cleaned


def file_stem_for_envelope(envelope: NatsEnvelope, *, config: FileSinkConfig) -> str:
    """Build the deterministic filename stem for one envelope.

    Missing key material is a permanent message problem for the selected file
    strategy.  The core runner can send that message to DLQ when configured,
    and it must not ACK until DLQ publication succeeds.
    """

    if config.filename_strategy == "stream_sequence":
        if not envelope.stream or envelope.stream_sequence is None:
            raise PermanentSinkError(
                "file sink filename_strategy='stream_sequence' requires stream metadata"
            )
        stream = safe_path_component(envelope.stream, fallback="stream")
        return f"{stream}-{envelope.stream_sequence:020d}"

    if config.filename_strategy == "message_id":
        if not envelope.message_id:
            raise PermanentSinkError(
                "file sink filename_strategy='message_id' requires a message ID"
            )
        message = safe_path_component(envelope.message_id, fallback="message")
        return f"{message}-{_digest(envelope.message_id)[:16]}"

    subject = safe_path_component(envelope.subject, fallback="subject")
    return f"{subject}-{_digest(envelope.data)}"


def relative_path_for_envelope(envelope: NatsEnvelope, *, config: FileSinkConfig) -> Path:
    """Return the relative output path for one envelope."""

    filename = f"{file_stem_for_envelope(envelope, config=config)}{config.extension}"
    if not config.partition_by_subject:
        return Path(filename)
    subject_partition = safe_path_component(envelope.subject, fallback="subject")
    return Path(subject_partition) / filename


def file_record_for_envelope(
    envelope: NatsEnvelope,
    *,
    config: FileSinkConfig,
    stored_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the JSON document written by `FileSink`.

    The `payload` value follows the same framework-level payload normalization
    contract used by Oracle and future JSON-capable sinks.  Metadata capture is
    enabled by default so file outputs preserve NATS headers, JetStream
    sequences, and epoch timing fields.
    """

    stored_at = stored_at or datetime.now(UTC)
    normalized_payload = envelope.payload_for_json_storage(mode=config.payload_mode)
    record: dict[str, Any] = {
        "schema": "nats_sinks.file.message.v1",
        "schema_version": 1,
        "subject": envelope.subject,
        "stream": envelope.stream,
        "stream_sequence": envelope.stream_sequence,
        "consumer": envelope.consumer,
        "consumer_sequence": envelope.consumer_sequence,
        "message_id": envelope.message_id,
        "payload": normalized_payload.value,
        "payload_info": {
            "original_format": normalized_payload.original_format,
            "wrapped": normalized_payload.wrapped,
            "sha256": normalized_payload.sha256,
            "size_bytes": normalized_payload.size_bytes,
        },
    }
    if config.include_metadata:
        record["metadata"] = envelope.metadata_for_json_storage(stored_at=stored_at)
    return record
