# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Deterministic synthetic test scenarios for nats-sinks.

The harness in this module creates fake `NatsEnvelope` objects that look like
messages a mission-support data pipeline might process, while deliberately
avoiding real operational content.  It is meant for repeatable release checks,
developer smoke tests, and future sink certification tests.

The generated messages cover normal and non-happy-path cases: malformed text
that resembles JSON, duplicate idempotency keys, stale event timestamps,
encryption-envelope-shaped payloads, priority metadata, classification
metadata, and labels.  Reports are sanitized by design and avoid raw payloads,
local filesystem paths, service URLs, IP addresses, usernames, credentials,
certificate material, and other deployment-specific details.
"""

from __future__ import annotations

import asyncio
import json
import random
import shutil
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from nats_sinks.core.encryption import (
    ENCRYPTED_PAYLOAD_KEY,
    ENCRYPTED_PAYLOAD_SCHEMA,
    ENCRYPTED_PAYLOAD_VERSION,
)
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.message_metadata import labels_to_storage_string
from nats_sinks.file import FileSink
from nats_sinks.file.config import FileCompression

SyntheticCase = Literal[
    "valid_json",
    "malformed_json_text",
    "duplicate",
    "stale",
    "encrypted_marker",
    "classified",
    "priority",
    "labeled",
    "empty",
]

DEFAULT_CASE_ORDER: tuple[SyntheticCase, ...] = (
    "valid_json",
    "malformed_json_text",
    "duplicate",
    "stale",
    "encrypted_marker",
    "classified",
    "priority",
    "labeled",
    "empty",
)
MAX_SYNTHETIC_MESSAGE_COUNT = 10_000


@dataclass(frozen=True, slots=True)
class SyntheticScenarioProfile:
    """Configuration for deterministic synthetic message generation.

    `message_count` controls the number of envelopes generated.  The case
    sequence cycles through `case_order`, so every profile can scale from a
    tiny smoke run to a larger local stress run while preserving deterministic
    behavior for the same seed.
    """

    name: str = "mission-smoke"
    message_count: int = 32
    seed: int = 42
    stream: str = "MISSION_SYNTHETIC"
    consumer: str = "mission-synthetic-sink"
    subject_prefix: str = "mission.synthetic"
    event_time: datetime = field(default_factory=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    stale_age: timedelta = timedelta(hours=6)
    default_priority: str | None = "routine"
    default_classification: str | None = "NATO RESTRICTED"
    default_labels: tuple[str, ...] = ("synthetic", "mission-test")
    case_order: tuple[SyntheticCase, ...] = DEFAULT_CASE_ORDER

    def __post_init__(self) -> None:
        """Validate profile bounds before any envelopes are generated."""

        if not self.name.strip():
            raise ValueError("Synthetic scenario profile name must not be empty.")
        if self.message_count < 1:
            raise ValueError("Synthetic scenario message_count must be at least 1.")
        if self.message_count > MAX_SYNTHETIC_MESSAGE_COUNT:
            raise ValueError("Synthetic scenario message_count must not exceed 10000.")
        if not self.stream.strip():
            raise ValueError("Synthetic scenario stream must not be empty.")
        if not self.consumer.strip():
            raise ValueError("Synthetic scenario consumer must not be empty.")
        if not self.subject_prefix.strip():
            raise ValueError("Synthetic scenario subject_prefix must not be empty.")
        if not self.case_order:
            raise ValueError("Synthetic scenario case_order must not be empty.")


@dataclass(frozen=True, slots=True)
class SyntheticMessage:
    """One generated message plus expectations used by test assertions."""

    case: SyntheticCase
    envelope: NatsEnvelope
    duplicate_of_sequence: int | None = None
    stale: bool = False
    encrypted_marker: bool = False
    malformed_json_text: bool = False


@dataclass(frozen=True, slots=True)
class SyntheticScenarioReport:
    """Sanitized summary of a synthetic test scenario.

    The report intentionally stores only counts, public profile names, case
    names, and storage-mode evidence.  Raw payloads, absolute paths, service
    locators, credentials, and private infrastructure details are excluded so
    the report can be copied into public issues and release notes.
    """

    profile: str
    sink: str
    generated_messages: int
    unique_idempotency_keys: int
    duplicate_messages: int
    cases: dict[str, int]
    priority_values: dict[str, int]
    classification_values: dict[str, int]
    labels: dict[str, int]
    stale_messages: int
    encrypted_marker_messages: int
    malformed_json_text_messages: int
    file_count: int | None = None
    compression: str | None = None
    report_schema: str = "nats_sinks.testing.synthetic_report.v1"

    def to_dict(self) -> dict[str, Any]:
        """Render the report as JSON-serializable data."""

        return {
            "report_schema": self.report_schema,
            "profile": self.profile,
            "sink": self.sink,
            "generated_messages": self.generated_messages,
            "unique_idempotency_keys": self.unique_idempotency_keys,
            "duplicate_messages": self.duplicate_messages,
            "cases": self.cases,
            "priority_values": self.priority_values,
            "classification_values": self.classification_values,
            "labels": self.labels,
            "stale_messages": self.stale_messages,
            "encrypted_marker_messages": self.encrypted_marker_messages,
            "malformed_json_text_messages": self.malformed_json_text_messages,
            "file_count": self.file_count,
            "compression": self.compression,
        }


@dataclass(frozen=True, slots=True)
class SyntheticFileSinkResult:
    """Result returned by the file-sink smoke harness."""

    report: SyntheticScenarioReport
    retained_output_directory: Path | None


def _case_for_index(profile: SyntheticScenarioProfile, index: int) -> SyntheticCase:
    """Return the configured case for a one-based message index."""

    return profile.case_order[(index - 1) % len(profile.case_order)]


def _subject(profile: SyntheticScenarioProfile, case: SyntheticCase, index: int) -> str:
    """Build a deterministic synthetic subject without real operational names."""

    lane = "sensor" if index % 2 else "command"
    return f"{profile.subject_prefix}.{lane}.{case.replace('_', '-')}.{index:04d}"


def _metadata_for_case(
    profile: SyntheticScenarioProfile,
    case: SyntheticCase,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Return priority, classification, and labels for a synthetic case."""

    priority = profile.default_priority
    classification = profile.default_classification
    labels = profile.default_labels
    if case == "priority":
        priority = "urgent"
    if case == "classified":
        classification = "NATO SECRET"
    if case == "labeled":
        labels = (*profile.default_labels, "sensor-fusion", "f2t2ea-example")
    return priority, classification, labels


def _json_payload(case: SyntheticCase, *, index: int, rng: random.Random) -> bytes:
    """Return a stable fake JSON payload for normal synthetic messages."""

    payload = {
        "synthetic": True,
        "case": case,
        "track_id": f"SYN-{index:04d}",
        "confidence": round(rng.uniform(0.50, 0.99), 3),
        "phase": "track" if index % 2 else "assess",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encrypted_marker_payload(index: int) -> bytes:
    """Return a fake encryption envelope without generating secret key material."""

    payload = {
        ENCRYPTED_PAYLOAD_KEY: {
            "schema": ENCRYPTED_PAYLOAD_SCHEMA,
            "version": ENCRYPTED_PAYLOAD_VERSION,
            "algorithm": "aes-256-gcm",
            "key_id": "synthetic-test-key",
            "nonce": "c3ludGhldGljLW5vbmNlLTEy",
            "nonce_size_bytes": 16,
            "ciphertext": f"synthetic-ciphertext-{index:04d}",
            "ciphertext_encoding": "base64",
            "tag_length": 16,
            "plaintext_sha256": "0" * 64,
            "plaintext_size_bytes": 128,
        }
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _payload_for_case(case: SyntheticCase, *, index: int, rng: random.Random) -> bytes:
    """Return payload bytes for one synthetic case."""

    if case == "malformed_json_text":
        return b'{"synthetic": true, "case": "malformed-json"'
    if case == "empty":
        return b""
    if case == "encrypted_marker":
        return _encrypted_marker_payload(index)
    return _json_payload(case, index=index, rng=rng)


def _stream_sequence_for_case(case: SyntheticCase, index: int) -> tuple[int, int | None]:
    """Return stream sequence and duplicate reference for one case."""

    if case == "duplicate" and index > 1:
        return index - 1, index - 1
    return index, None


def generate_synthetic_scenario(
    profile: SyntheticScenarioProfile | None = None,
) -> list[SyntheticMessage]:
    """Generate a deterministic list of synthetic messages.

    The function creates only in-memory `NatsEnvelope` objects.  It never opens
    a network connection, never reads environment variables, and never writes
    files.  That makes it safe for unit tests and future sink certification
    harnesses.
    """

    profile = profile or SyntheticScenarioProfile()
    rng = random.Random(profile.seed)  # nosec B311  # noqa: S311
    messages: list[SyntheticMessage] = []
    by_sequence: dict[int, NatsEnvelope] = {}
    for index in range(1, profile.message_count + 1):
        case = _case_for_index(profile, index)
        stream_sequence, duplicate_of = _stream_sequence_for_case(case, index)
        if duplicate_of is not None and duplicate_of in by_sequence:
            original = by_sequence[duplicate_of]
            envelope = replace(
                original,
                consumer_sequence=index,
                pending=max(profile.message_count - index, 0),
                redelivered=True,
            )
            messages.append(
                SyntheticMessage(
                    case=case,
                    envelope=envelope,
                    duplicate_of_sequence=duplicate_of,
                    stale=False,
                    encrypted_marker=False,
                    malformed_json_text=False,
                )
            )
            continue

        stale = case == "stale"
        timestamp = profile.event_time - profile.stale_age if stale else profile.event_time
        priority, classification, labels = _metadata_for_case(profile, case)
        headers = {
            "Nats-Msg-Id": f"synthetic-{stream_sequence:08d}",
            "Nats-Sinks-Priority": priority or "",
            "Nats-Sinks-Classification": classification or "",
            "Nats-Sinks-Labels": labels_to_storage_string(labels) or "",
        }
        envelope = NatsEnvelope(
            subject=_subject(profile, case, index),
            data=_payload_for_case(case, index=index, rng=rng),
            headers=headers,
            stream=profile.stream,
            consumer=profile.consumer,
            stream_sequence=stream_sequence,
            consumer_sequence=index,
            timestamp=timestamp,
            message_id=f"synthetic-{stream_sequence:08d}",
            redelivered=duplicate_of is not None,
            pending=max(profile.message_count - index, 0),
            priority=priority,
            classification=classification,
            labels=labels,
        )
        messages.append(
            SyntheticMessage(
                case=case,
                envelope=envelope,
                duplicate_of_sequence=duplicate_of,
                stale=stale,
                encrypted_marker=case == "encrypted_marker",
                malformed_json_text=case == "malformed_json_text",
            )
        )
        by_sequence[stream_sequence] = envelope
    return messages


def _counter_with_null(values: Sequence[str | None]) -> dict[str, int]:
    """Count values while rendering missing values explicitly as `null`."""

    return dict(sorted(Counter(value if value is not None else "null" for value in values).items()))


def _label_counter(messages: Sequence[SyntheticMessage]) -> dict[str, int]:
    """Count synthetic labels without exposing payloads or infrastructure."""

    counter: Counter[str] = Counter()
    for message in messages:
        if not message.envelope.labels:
            counter["null"] += 1
            continue
        counter.update(message.envelope.labels)
    return dict(sorted(counter.items()))


def synthetic_report(
    messages: Sequence[SyntheticMessage],
    *,
    profile_name: str,
    sink: str = "core",
    file_count: int | None = None,
    compression: str | None = None,
) -> SyntheticScenarioReport:
    """Build a sanitized report for generated messages or a sink smoke run."""

    idempotency_keys = [message.envelope.idempotency_key() for message in messages]
    duplicate_messages = len(idempotency_keys) - len(set(idempotency_keys))
    return SyntheticScenarioReport(
        profile=profile_name,
        sink=sink,
        generated_messages=len(messages),
        unique_idempotency_keys=len(set(idempotency_keys)),
        duplicate_messages=duplicate_messages,
        cases=dict(sorted(Counter(message.case for message in messages).items())),
        priority_values=_counter_with_null([message.envelope.priority for message in messages]),
        classification_values=_counter_with_null(
            [message.envelope.classification for message in messages]
        ),
        labels=_label_counter(messages),
        stale_messages=sum(1 for message in messages if message.stale),
        encrypted_marker_messages=sum(1 for message in messages if message.encrypted_marker),
        malformed_json_text_messages=sum(1 for message in messages if message.malformed_json_text),
        file_count=file_count,
        compression=compression,
    )


def render_synthetic_report_markdown(report: SyntheticScenarioReport) -> str:
    """Render sanitized report data as compact Markdown for release evidence."""

    data = report.to_dict()
    lines = [
        "# Synthetic Scenario Report",
        "",
        f"- Profile: `{data['profile']}`",
        f"- Sink: `{data['sink']}`",
        f"- Generated messages: `{data['generated_messages']}`",
        f"- Unique idempotency keys: `{data['unique_idempotency_keys']}`",
        f"- Duplicate messages: `{data['duplicate_messages']}`",
        f"- Stale messages: `{data['stale_messages']}`",
        f"- Encrypted-marker messages: `{data['encrypted_marker_messages']}`",
        f"- Malformed JSON text messages: `{data['malformed_json_text_messages']}`",
    ]
    if report.file_count is not None:
        lines.append(f"- Durable files: `{report.file_count}`")
    if report.compression is not None:
        lines.append(f"- Compression: `{report.compression}`")

    lines.extend(["", "## Cases", "", "| Case | Count |", "| --- | ---: |"])
    lines.extend(f"| `{case}` | {count} |" for case, count in report.cases.items())
    lines.extend(["", "## Metadata Values", "", "### Priority", ""])
    lines.extend(_markdown_counter_table(report.priority_values))
    lines.extend(["", "### Classification", ""])
    lines.extend(_markdown_counter_table(report.classification_values))
    lines.extend(["", "### Labels", ""])
    lines.extend(_markdown_counter_table(report.labels))
    return "\n".join(lines) + "\n"


def _markdown_counter_table(values: dict[str, int]) -> list[str]:
    """Render a simple two-column count table."""

    rows = ["| Value | Count |", "| --- | ---: |"]
    rows.extend(f"| `{value}` | {count} |" for value, count in values.items())
    return rows


async def _run_file_sink_synthetic_scenario_async(
    *,
    profile: SyntheticScenarioProfile,
    output_dir: Path | None,
    compression: FileCompression,
    preserve_files: bool,
) -> SyntheticFileSinkResult:
    """Run the synthetic messages through `FileSink` without external services."""

    messages = generate_synthetic_scenario(profile)
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if output_dir is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="nats-sinks-synthetic-")
        root = Path(temp_dir.name)
    else:
        root = output_dir

    sink = FileSink(directory=root, fsync=False, compression=compression)
    try:
        await sink.start()
        await sink.write_batch([message.envelope for message in messages])
        suffix = ".json.gz" if compression == "gzip" else ".json"
        file_count = sum(1 for path in root.rglob(f"*{suffix}") if path.is_file())
        report = synthetic_report(
            messages,
            profile_name=profile.name,
            sink="file",
            file_count=file_count,
            compression=compression,
        )
        retained = root if preserve_files else None
        return SyntheticFileSinkResult(report=report, retained_output_directory=retained)
    finally:
        if temp_dir is not None and not preserve_files:
            temp_dir.cleanup()
        elif temp_dir is None and not preserve_files:
            shutil.rmtree(root, ignore_errors=True)


def run_file_sink_synthetic_scenario(
    *,
    profile: SyntheticScenarioProfile | None = None,
    output_dir: Path | None = None,
    compression: FileCompression = "none",
    preserve_files: bool = False,
) -> SyntheticFileSinkResult:
    """Run the file-sink synthetic harness from synchronous tools and tests."""

    return asyncio.run(
        _run_file_sink_synthetic_scenario_async(
            profile=profile or SyntheticScenarioProfile(),
            output_dir=output_dir,
            compression=compression,
            preserve_files=preserve_files,
        )
    )
