# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import gzip
import json
import os
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from nats_sinks.core.runner import JetStreamSinkRunner
from nats_sinks.file import FileSink


@dataclass
class FakeSequence:
    stream: int
    consumer: int


@dataclass
class FakeMetadata:
    stream: str = "ORDERS"
    consumer: str = "file-orders-sink"
    sequence: FakeSequence = field(default_factory=lambda: FakeSequence(stream=1, consumer=1))
    num_delivered: int = 1
    num_pending: int = 0


class FakeMessage:
    def __init__(self, events: list[str], *, sequence: int, data: bytes) -> None:
        self.subject = "orders.created"
        self.data = data
        self.headers = {"Nats-Msg-Id": f"file-e2e-{sequence}"}
        self.metadata = FakeMetadata(sequence=FakeSequence(stream=sequence, consumer=sequence))
        self.events = events
        self.acked = False

    async def ack(self) -> None:
        self.events.append(f"ack-{self.metadata.sequence.stream}")
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        del delay
        self.events.append(f"nak-{self.metadata.sequence.stream}")


def _json_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json*") if path.is_file())


def _read_file_record(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    if path.name.endswith(".gz"):
        data = gzip.decompress(data)
    loaded = json.loads(data.decode("utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _delete_after_file_e2e() -> bool:
    value = os.getenv("NATS_SINKS_FILE_E2E_DELETE_AFTER", "true")
    return value.lower() in {"1", "true", "yes", "on"}


def _file_e2e_directory(tmp_path: Path, *, compression: str) -> Path:
    configured = os.getenv("NATS_SINKS_FILE_E2E_DIRECTORY")
    if configured is None:
        return tmp_path
    return Path(configured).expanduser() / f"{compression}-{uuid.uuid4().hex}"


def _cleanup_file_e2e_directory(path: Path, *, tmp_path: Path) -> None:
    if _delete_after_file_e2e() and path != tmp_path:
        shutil.rmtree(path, ignore_errors=True)


async def _run_file_sink_e2e(
    *,
    output_dir: Path,
    compression: str = "none",
) -> tuple[list[Path], list[dict[str, object]], list[str]]:
    events: list[str] = []
    messages: Sequence[FakeMessage] = [
        FakeMessage(events, sequence=1, data=b'{"order_id":"O-1001"}'),
        FakeMessage(events, sequence=2, data=b"encrypted-text"),
        FakeMessage(events, sequence=3, data=b""),
        FakeMessage(events, sequence=4, data=b"\xff\x00\xfe"),
    ]
    sink = FileSink(directory=output_dir, fsync=False, compression=compression)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="file-orders-sink",
        subject="orders.*",
        sink=sink,
    )

    await sink.start()
    await runner.process_raw_batch(messages)

    assert [message.acked for message in messages] == [True, True, True, True]
    files = _json_files(output_dir)
    records = [_read_file_record(path) for path in files]
    return files, records, events


async def test_runner_file_sink_local_end_to_end(tmp_path: Path) -> None:
    """Exercise runner -> FileSink -> durable files -> ACK without external services."""

    output_dir = _file_e2e_directory(tmp_path, compression="none")
    try:
        files, records, events = await _run_file_sink_e2e(output_dir=output_dir)

        assert events == ["ack-1", "ack-2", "ack-3", "ack-4"]
        assert len(files) == 4
        assert all(path.name.endswith(".json") for path in files)
        assert records[0]["payload"] == {"order_id": "O-1001"}
        assert records[1]["payload"]["_nats_sinks"]["payload_format"] == "text"
        assert records[2]["payload"]["_nats_sinks"]["size_bytes"] == 0
        assert records[3]["payload"]["_nats_sinks"]["payload_format"] == "bytes"
        assert all(record["metadata"]["jetstream"]["stream"] == "ORDERS" for record in records)
    finally:
        _cleanup_file_e2e_directory(output_dir, tmp_path=tmp_path)


async def test_runner_file_sink_local_end_to_end_with_gzip(tmp_path: Path) -> None:
    """Exercise compressed file writes over multiple output files."""

    output_dir = _file_e2e_directory(tmp_path, compression="gzip")
    try:
        files, records, events = await _run_file_sink_e2e(
            output_dir=output_dir,
            compression="gzip",
        )

        assert events == ["ack-1", "ack-2", "ack-3", "ack-4"]
        assert len(files) == 4
        assert all(path.name.endswith(".json.gz") for path in files)
        assert records[0]["payload"] == {"order_id": "O-1001"}
        assert records[1]["payload"]["_nats_sinks"]["payload_format"] == "text"
        assert records[2]["payload"]["_nats_sinks"]["size_bytes"] == 0
        assert records[3]["payload"]["_nats_sinks"]["payload_format"] == "bytes"
        assert all(record["metadata"]["jetstream"]["stream"] == "ORDERS" for record in records)
    finally:
        _cleanup_file_e2e_directory(output_dir, tmp_path=tmp_path)
