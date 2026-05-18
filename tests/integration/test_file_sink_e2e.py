# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
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
    return sorted(path for path in root.rglob("*.json") if path.is_file())


async def test_runner_file_sink_local_end_to_end(tmp_path: Path) -> None:
    """Exercise runner -> FileSink -> durable files -> ACK without external services."""

    events: list[str] = []
    messages: Sequence[FakeMessage] = [
        FakeMessage(events, sequence=1, data=b'{"order_id":"O-1001"}'),
        FakeMessage(events, sequence=2, data=b"encrypted-text"),
        FakeMessage(events, sequence=3, data=b""),
        FakeMessage(events, sequence=4, data=b"\xff\x00\xfe"),
    ]
    sink = FileSink(directory=tmp_path, fsync=False)
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
    assert events == ["ack-1", "ack-2", "ack-3", "ack-4"]

    files = _json_files(tmp_path)
    assert len(files) == 4
    records = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert records[0]["payload"] == {"order_id": "O-1001"}
    assert records[1]["payload"]["_nats_sinks"]["payload_format"] == "text"
    assert records[2]["payload"]["_nats_sinks"]["size_bytes"] == 0
    assert records[3]["payload"]["_nats_sinks"]["payload_format"] == "bytes"
    assert all(record["metadata"]["jetstream"]["stream"] == "ORDERS" for record in records)
