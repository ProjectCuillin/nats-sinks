# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nats_sinks import NatsEnvelope, PermanentSinkError
from nats_sinks.file.config import FileSinkConfig
from nats_sinks.file.mapping import (
    file_record_for_envelope,
    file_stem_for_envelope,
    relative_path_for_envelope,
    safe_path_component,
)


def _envelope(
    *,
    subject: str = "orders.created",
    data: bytes = b'{"order_id":"O-1001"}',
    stream: str | None = "ORDERS",
    stream_sequence: int | None = 7,
    message_id: str | None = "msg-1",
    priority: str | None = None,
    classification: str | None = None,
    labels: object | None = None,
) -> NatsEnvelope:
    return NatsEnvelope(
        subject=subject,
        data=data,
        headers={"Nats-Msg-Id": message_id} if message_id else {},
        stream=stream,
        consumer="file-sink",
        stream_sequence=stream_sequence,
        consumer_sequence=3,
        timestamp=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        message_id=message_id,
        redelivered=False,
        pending=0,
        priority=priority,
        classification=classification,
        labels=labels or (),
    )


def test_stream_sequence_filename_is_deterministic() -> None:
    config = FileSinkConfig(directory=Path("test-output"))

    assert file_stem_for_envelope(_envelope(), config=config) == "ORDERS-00000000000000000007"


def test_stream_sequence_filename_requires_jetstream_metadata() -> None:
    config = FileSinkConfig(directory=Path("test-output"))

    with pytest.raises(PermanentSinkError, match="requires stream metadata"):
        file_stem_for_envelope(
            _envelope(stream=None, stream_sequence=None),
            config=config,
        )


def test_message_id_filename_requires_message_id() -> None:
    config = FileSinkConfig(directory=Path("test-output"), filename_strategy="message_id")

    with pytest.raises(PermanentSinkError, match="requires a message ID"):
        file_stem_for_envelope(_envelope(message_id=None), config=config)


def test_payload_sha256_filename_always_has_stable_key() -> None:
    config = FileSinkConfig(directory=Path("test-output"), filename_strategy="payload_sha256")

    first = file_stem_for_envelope(_envelope(data=b"same"), config=config)
    second = file_stem_for_envelope(_envelope(data=b"same"), config=config)

    assert first == second
    assert first.startswith("orders.created-")


def test_relative_path_partitions_by_sanitized_subject() -> None:
    config = FileSinkConfig(directory=Path("test-output"))
    envelope = _envelope(subject="../orders/created urgent")

    relative = relative_path_for_envelope(envelope, config=config)

    assert relative.parts[0] == "orders_created_urgent"
    assert relative.name == "ORDERS-00000000000000000007.json"
    assert ".." not in relative.parts


def test_gzip_compression_defaults_to_gzip_extension() -> None:
    config = FileSinkConfig(directory=Path("test-output"), compression="gzip")

    assert config.extension == ".json.gz"
    assert relative_path_for_envelope(_envelope(), config=config).name.endswith(".json.gz")


def test_gzip_compression_respects_explicit_extension() -> None:
    config = FileSinkConfig(
        directory=Path("test-output"),
        compression="gzip",
        extension=".event.gz",
    )

    assert config.extension == ".event.gz"
    assert relative_path_for_envelope(_envelope(), config=config).name.endswith(".event.gz")


def test_gzip_compression_level_is_validated() -> None:
    with pytest.raises(ValueError, match="compression_level"):
        FileSinkConfig(directory=Path("test-output"), compression="gzip", compression_level=0)


def test_safe_path_component_fuzz_cases_do_not_escape() -> None:
    values = [
        "",
        ".",
        "..",
        "../secret",
        "subject/with/slash",
        "subject\\with\\backslash",
        "subject with spaces",
        "🔥" * 20,
        "a" * 400,
        "\x00bad",
    ]

    for value in values:
        component = safe_path_component(value)
        assert component
        assert component not in {".", ".."}
        assert "/" not in component
        assert "\\" not in component
        assert len(component) <= 120


def test_file_record_preserves_json_payload_and_metadata() -> None:
    config = FileSinkConfig(directory=Path("test-output"))
    record = file_record_for_envelope(
        _envelope(priority="urgent", classification="restricted", labels=("billing", "urgent")),
        config=config,
    )

    json.dumps(record)

    assert record["schema"] == "nats_sinks.file.message.v1"
    assert record["priority"] == "urgent"
    assert record["classification"] == "restricted"
    assert record["labels"] == "billing;urgent"
    assert record["labels_list"] == ["billing", "urgent"]
    assert record["payload"] == {"order_id": "O-1001"}
    assert record["payload_info"]["original_format"] == "json"
    assert record["metadata"]["jetstream"]["stream_sequence"] == 7
    assert record["metadata"]["message_metadata"]["priority"] == "urgent"
    assert record["metadata"]["message_metadata"]["classification"] == "restricted"
    assert record["metadata"]["message_metadata"]["labels"] == ["billing", "urgent"]


def test_file_record_stores_missing_message_metadata_as_null() -> None:
    config = FileSinkConfig(directory=Path("test-output"))
    record = file_record_for_envelope(_envelope(), config=config)

    assert record["priority"] is None
    assert record["classification"] is None
    assert record["labels"] is None
    assert record["labels_list"] == []
    assert record["metadata"]["message_metadata"] == {
        "priority": None,
        "classification": None,
        "labels": [],
    }


def test_file_record_wraps_text_payload() -> None:
    config = FileSinkConfig(directory=Path("test-output"))
    record = file_record_for_envelope(_envelope(data=b"encrypted-text"), config=config)

    assert record["payload"]["_nats_sinks"]["payload_format"] == "text"
    assert record["payload"]["payload"] == "encrypted-text"


def test_file_record_wraps_empty_payload() -> None:
    config = FileSinkConfig(directory=Path("test-output"))
    record = file_record_for_envelope(_envelope(data=b""), config=config)

    assert record["payload"]["_nats_sinks"]["size_bytes"] == 0
    assert record["payload_info"]["wrapped"]
