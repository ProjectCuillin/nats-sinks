# SPDX-License-Identifier: Apache-2.0
"""Shared payload normalization for sinks that store JSON-compatible values.

NATS message bodies are bytes.  Some destinations, including the first Oracle
sink, store payloads in JSON-capable columns so operators can query the data
later.  This module defines the framework-level contract for turning arbitrary
message bytes into a JSON value without logging or discarding sensitive data.

The default mode keeps valid JSON unchanged.  If the payload is not JSON but is
UTF-8 text, it is wrapped in a small JSON envelope.  If the payload is not text,
it can be wrapped as base64 bytes.  That lets encrypted text or opaque binary
payloads be persisted durably while still giving every backend a JSON value to
store.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from nats_sinks.core.errors import SerializationError

PayloadStorageMode = Literal["json_or_envelope", "json_only", "text_envelope", "bytes_envelope"]
PayloadOriginalFormat = Literal["json", "text", "bytes"]

PAYLOAD_ENVELOPE_KEY = "_nats_sinks"
PAYLOAD_VALUE_KEY = "payload"
PAYLOAD_ENVELOPE_VERSION = 1


@dataclass(frozen=True, slots=True)
class NormalizedPayload:
    """A JSON-compatible payload value plus normalization metadata.

    `value` is safe to pass to `json.dumps`.  It is either the original parsed
    JSON value or a framework-defined JSON envelope containing the original
    text/base64 payload.  The metadata fields are useful for tests, metrics, and
    future sinks, but they do not contain the raw payload.
    """

    value: Any
    original_format: PayloadOriginalFormat
    wrapped: bool
    sha256: str
    size_bytes: int


def _payload_digest(data: bytes) -> str:
    """Return a stable digest used for diagnostics and duplicate analysis."""

    return hashlib.sha256(data).hexdigest()


def _payload_envelope(
    *,
    payload: str,
    payload_format: PayloadOriginalFormat,
    payload_encoding: str,
    sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    """Build the standard JSON envelope used by current and future sinks."""

    return {
        PAYLOAD_ENVELOPE_KEY: {
            "payload_envelope_version": PAYLOAD_ENVELOPE_VERSION,
            "payload_format": payload_format,
            "payload_encoding": payload_encoding,
            "sha256": sha256,
            "size_bytes": size_bytes,
        },
        PAYLOAD_VALUE_KEY: payload,
    }


def _text_payload(data: bytes, *, subject: str, sha256: str) -> NormalizedPayload:
    """Decode and wrap UTF-8 text while keeping raw content out of errors."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"message payload for subject {subject!r} is not valid UTF-8 text"
        raise SerializationError(msg) from exc
    return NormalizedPayload(
        value=_payload_envelope(
            payload=text,
            payload_format="text",
            payload_encoding="utf-8",
            sha256=sha256,
            size_bytes=len(data),
        ),
        original_format="text",
        wrapped=True,
        sha256=sha256,
        size_bytes=len(data),
    )


def _bytes_payload(data: bytes, *, sha256: str) -> NormalizedPayload:
    """Wrap arbitrary bytes as base64 in the standard JSON payload envelope."""

    encoded = base64.b64encode(data).decode("ascii")
    return NormalizedPayload(
        value=_payload_envelope(
            payload=encoded,
            payload_format="bytes",
            payload_encoding="base64",
            sha256=sha256,
            size_bytes=len(data),
        ),
        original_format="bytes",
        wrapped=True,
        sha256=sha256,
        size_bytes=len(data),
    )


def normalize_payload_for_json_storage(
    data: bytes,
    *,
    subject: str,
    mode: PayloadStorageMode = "json_or_envelope",
) -> NormalizedPayload:
    """Normalize message bytes into a JSON-compatible value.

    Modes:

    - `json_or_envelope`: keep valid JSON as-is, wrap UTF-8 text, and wrap
      non-text bytes as base64. This is the default for mixed payload streams.
    - `json_only`: require the payload to be valid JSON and raise
      `SerializationError` otherwise.
    - `text_envelope`: treat every payload as UTF-8 text and wrap it. This is
      useful for encrypted text streams because it avoids failed JSON parsing.
    - `bytes_envelope`: treat every payload as bytes and wrap it as base64.

    Error messages include the subject but never include the payload itself.
    """

    sha256 = _payload_digest(data)

    if mode == "bytes_envelope":
        return _bytes_payload(data, sha256=sha256)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if mode == "json_or_envelope":
            return _bytes_payload(data, sha256=sha256)
        msg = f"message payload for subject {subject!r} is not valid UTF-8"
        raise SerializationError(msg) from exc

    if mode == "text_envelope":
        return _text_payload(data, subject=subject, sha256=sha256)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        if mode == "json_only":
            msg = f"message payload for subject {subject!r} is not valid JSON"
            raise SerializationError(msg) from exc
        return _text_payload(data, subject=subject, sha256=sha256)

    return NormalizedPayload(
        value=payload,
        original_format="json",
        wrapped=False,
        sha256=sha256,
        size_bytes=len(data),
    )
