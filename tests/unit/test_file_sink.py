# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nats_sinks import DestinationUnavailableError, NatsEnvelope, PermanentSinkError
from nats_sinks.core.errors import ConfigurationError
from nats_sinks.file import FileSink


def _envelope(
    *,
    subject: str = "orders.created",
    data: bytes = b'{"order_id":"O-1001"}',
    stream: str | None = "ORDERS",
    stream_sequence: int | None = 1,
    message_id: str | None = "msg-1",
) -> NatsEnvelope:
    return NatsEnvelope(
        subject=subject,
        data=data,
        headers={"Nats-Msg-Id": message_id} if message_id else {},
        stream=stream,
        consumer="file-sink",
        stream_sequence=stream_sequence,
        consumer_sequence=1,
        timestamp=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        message_id=message_id,
        redelivered=False,
        pending=0,
    )


def _json_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


async def test_file_sink_writes_one_json_file_per_message(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False)

    await sink.start()
    await sink.write_batch([_envelope(stream_sequence=1), _envelope(stream_sequence=2)])

    files = _json_files(tmp_path)
    assert [path.name for path in files] == [
        "ORDERS-00000000000000000001.json",
        "ORDERS-00000000000000000002.json",
    ]
    first = json.loads(files[0].read_text(encoding="utf-8"))
    assert first["payload"] == {"order_id": "O-1001"}
    assert first["metadata"]["subject"] == "orders.created"


async def test_file_sink_skips_existing_duplicates_by_default(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False)
    message = _envelope(stream_sequence=1, data=b'{"version":1}')

    await sink.start()
    await sink.write_batch([message])
    path = _json_files(tmp_path)[0]
    before = path.read_text(encoding="utf-8")
    await sink.write_batch([message])

    assert path.read_text(encoding="utf-8") == before
    assert len(_json_files(tmp_path)) == 1


async def test_file_sink_can_overwrite_existing_file_when_configured(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False, duplicate_policy="overwrite")

    await sink.start()
    await sink.write_batch([_envelope(stream_sequence=1, data=b'{"version":1}')])
    await sink.write_batch([_envelope(stream_sequence=1, data=b'{"version":2}')])

    payload = json.loads(_json_files(tmp_path)[0].read_text(encoding="utf-8"))["payload"]
    assert payload == {"version": 2}


async def test_file_sink_can_fail_on_existing_duplicate(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False, duplicate_policy="fail")
    message = _envelope(stream_sequence=1)

    await sink.start()
    await sink.write_batch([message])

    with pytest.raises(PermanentSinkError, match="already exists"):
        await sink.write_batch([message])


async def test_file_sink_message_id_strategy(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False, filename_strategy="message_id")

    await sink.start()
    await sink.write_batch([_envelope(message_id="client/id:1")])

    files = _json_files(tmp_path)
    assert len(files) == 1
    assert files[0].name.startswith("client_id_1-")


async def test_file_sink_missing_message_id_is_permanent_failure(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False, filename_strategy="message_id")

    await sink.start()
    with pytest.raises(PermanentSinkError, match="requires a message ID"):
        await sink.write_batch([_envelope(message_id=None)])


async def test_file_sink_subject_path_traversal_is_sanitized(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False)

    await sink.start()
    await sink.write_batch([_envelope(subject="../../private/orders")])

    files = _json_files(tmp_path)
    assert len(files) == 1
    assert files[0].is_relative_to(tmp_path)
    assert ".." not in files[0].relative_to(tmp_path).parts


async def test_file_sink_non_utf8_payload_is_base64_wrapped(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False)

    await sink.start()
    await sink.write_batch([_envelope(data=b"\xff\x00\xfe")])

    record = json.loads(_json_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert record["payload"]["_nats_sinks"]["payload_format"] == "bytes"
    assert record["payload_info"]["original_format"] == "bytes"


async def test_file_sink_healthcheck_leaves_no_probe_files(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False)

    await sink.start()
    await sink.healthcheck()

    assert list(tmp_path.rglob(".nats-sinks-healthcheck.*")) == []


async def test_file_sink_rejects_file_as_directory(tmp_path: Path) -> None:
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("not a directory", encoding="utf-8")
    sink = FileSink(directory=not_a_directory, fsync=False)

    with pytest.raises(ConfigurationError, match="is not a directory"):
        await sink.start()


async def test_file_sink_reports_unavailable_missing_directory_when_create_disabled(
    tmp_path: Path,
) -> None:
    sink = FileSink(directory=tmp_path / "missing", create_directory=False, fsync=False)

    with pytest.raises(ConfigurationError, match="does not exist"):
        await sink.start()


async def test_file_sink_wraps_destination_os_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = FileSink(directory=tmp_path, fsync=False)
    await sink.start()

    def fail_mkstemp(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise OSError("disk full")

    monkeypatch.setattr("nats_sinks.file.sink.tempfile.mkstemp", fail_mkstemp)

    with pytest.raises(DestinationUnavailableError, match="failed to write"):
        await sink.write_batch([_envelope()])


async def test_file_sink_fuzz_subjects_never_write_outside_root(tmp_path: Path) -> None:
    sink = FileSink(directory=tmp_path, fsync=False, filename_strategy="payload_sha256")
    subjects = [
        "../escape",
        "..\\escape",
        "/absolute/path",
        "orders.created",
        "orders created urgent",
        "orders/created/urgent",
        "orders\x00created",
        "🔥.orders.created",
        "." * 50,
        "a" * 300,
    ]

    await sink.start()
    for index, subject in enumerate(subjects):
        await sink.write_batch(
            [_envelope(subject=subject, data=f"payload-{index}".encode(), stream_sequence=index)]
        )

    files = _json_files(tmp_path)
    assert len(files) == len(subjects)
    for path in files:
        assert path.is_relative_to(tmp_path)
        assert ".." not in path.relative_to(tmp_path).parts
