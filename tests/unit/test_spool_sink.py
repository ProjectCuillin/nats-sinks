# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for encrypted edge spool-and-forward behavior."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import pytest
from typer.testing import CliRunner

from nats_sinks import NatsEnvelope
from nats_sinks.cli.main import app
from nats_sinks.core.errors import DestinationUnavailableError, PermanentSinkError
from nats_sinks.spool import SpoolSink, replay_spool_to_sink
from nats_sinks.spool.config import SpoolSinkConfig

T = TypeVar("T")


class MemorySink:
    """Tiny test sink that records replayed messages."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[NatsEnvelope] = []

    async def start(self) -> None:
        pass

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        if self.fail:
            raise DestinationUnavailableError("target sink unavailable")
        self.messages.extend(messages)

    async def stop(self) -> None:
        pass


def _envelope(
    *,
    subject: str = "orders.created",
    data: bytes = b'{"order_id":"O-1001"}',
    stream_sequence: int = 1,
    priority: str | None = "normal",
    classification: str | None = "NATO UNCLASSIFIED",
    labels: tuple[str, ...] = ("edge", "spool"),
) -> NatsEnvelope:
    return NatsEnvelope(
        subject=subject,
        data=data,
        headers={"Nats-Msg-Id": f"msg-{stream_sequence}"},
        stream="ORDERS",
        consumer="spool-sink",
        stream_sequence=stream_sequence,
        consumer_sequence=stream_sequence,
        timestamp=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        message_id=f"msg-{stream_sequence}",
        redelivered=False,
        pending=0,
        priority=priority,
        classification=classification,
        labels=labels,
        mission_metadata={"profile": "mission-event-v1", "phase": "track"},
    )


def _key_b64() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _encryption() -> dict[str, object]:
    return {
        "enabled": True,
        "algorithm": "aes-256-gcm",
        "key_id": "spool-test-key",
        "key_b64": os.getenv("NATS_SINKS_TEST_SPOOL_KEY_B64") or _key_b64(),
    }


def _spool_files(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("*.spool.json") if path.is_file())


def test_spool_config_requires_encryption_by_default(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"requires sink\.encryption\.enabled=true"):
        SpoolSinkConfig(type="spool", directory=tmp_path)


async def test_spool_sink_writes_encrypted_record_without_plaintext_payload(tmp_path: Path) -> None:
    sink = SpoolSink(directory=tmp_path, fsync=False, encryption=_encryption())
    message = _envelope(data=b"encrypted edge payload")

    await sink.start()
    await sink.write_batch([message])

    files = await asyncio_to_thread(_spool_files, tmp_path)
    assert len(files) == 1
    raw = await asyncio_to_thread(files[0].read_bytes)
    assert b"encrypted edge payload" not in raw
    assert b"NATO UNCLASSIFIED" not in raw

    replayed = await asyncio_to_thread(sink.load_envelope, files[0])
    assert replayed.data == b"encrypted edge payload"
    assert replayed.idempotency_key() == message.idempotency_key()
    assert replayed.classification == "NATO UNCLASSIFIED"
    assert replayed.labels == ("edge", "spool")


async def test_spool_sink_skips_existing_duplicate_by_default(tmp_path: Path) -> None:
    sink = SpoolSink(directory=tmp_path, fsync=False, encryption=_encryption())
    message = _envelope(stream_sequence=10)

    await sink.start()
    await sink.write_batch([message])
    files_before = await asyncio_to_thread(_spool_files, tmp_path)
    before = await asyncio_to_thread(files_before[0].read_bytes)
    await sink.write_batch([message])

    files_after = await asyncio_to_thread(_spool_files, tmp_path)
    after = await asyncio_to_thread(files_after[0].read_bytes)
    assert len(files_after) == 1
    assert after == before


async def test_spool_sink_can_fail_on_existing_duplicate(tmp_path: Path) -> None:
    sink = SpoolSink(
        directory=tmp_path,
        fsync=False,
        encryption=_encryption(),
        duplicate_policy="fail",
    )
    message = _envelope(stream_sequence=11)

    await sink.start()
    await sink.write_batch([message])

    with pytest.raises(PermanentSinkError, match="already exists"):
        await sink.write_batch([message])


async def test_spool_sink_rejects_full_record_limit_without_partial_write(tmp_path: Path) -> None:
    sink = SpoolSink(directory=tmp_path, fsync=False, encryption=_encryption(), max_records=1)

    await sink.start()

    with pytest.raises(DestinationUnavailableError, match="record limit"):
        await sink.write_batch([_envelope(stream_sequence=1), _envelope(stream_sequence=2)])

    files = await asyncio_to_thread(_spool_files, tmp_path)
    assert files == []


async def test_spool_replay_writes_target_and_deletes_after_success(tmp_path: Path) -> None:
    spool = SpoolSink(directory=tmp_path, fsync=False, encryption=_encryption())
    target = MemorySink()

    await spool.start()
    await spool.write_batch([_envelope(stream_sequence=1)])
    result = await replay_spool_to_sink(spool, target)

    assert result.scanned_records == 1
    assert result.replayed_records == 1
    assert result.deleted_records == 1
    assert target.messages[0].data == b'{"order_id":"O-1001"}'
    assert await asyncio_to_thread(_spool_files, tmp_path) == []


async def test_spool_replay_keeps_file_when_target_fails(tmp_path: Path) -> None:
    spool = SpoolSink(directory=tmp_path, fsync=False, encryption=_encryption())
    target = MemorySink(fail=True)

    await spool.start()
    await spool.write_batch([_envelope(stream_sequence=1)])

    with pytest.raises(DestinationUnavailableError, match="target sink unavailable"):
        await replay_spool_to_sink(spool, target)

    assert len(await asyncio_to_thread(_spool_files, tmp_path)) == 1


async def test_spool_replay_drains_high_priority_first(tmp_path: Path) -> None:
    spool = SpoolSink(directory=tmp_path, fsync=False, encryption=_encryption())
    target = MemorySink()

    await spool.start()
    await spool.write_batch(
        [
            _envelope(stream_sequence=1, priority="low"),
            _envelope(stream_sequence=2, priority="urgent"),
            _envelope(stream_sequence=3, priority="normal"),
        ]
    )
    await replay_spool_to_sink(spool, target)

    assert [message.stream_sequence for message in target.messages] == [2, 3, 1]


def test_cli_validates_spool_sink_config(tmp_path: Path) -> None:
    config = tmp_path / "spool-config.json"
    config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "spool-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "spool",
                    "directory": str(tmp_path / "spool"),
                    "fsync": False,
                    "encryption": _encryption(),
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["validate", str(config)])

    assert result.exit_code == 0
    assert "Active sink: spool" in result.output


def test_cli_replay_spool_dry_run_counts_records(tmp_path: Path) -> None:
    async def _prepare() -> None:
        sink = SpoolSink(directory=tmp_path / "spool", fsync=False, encryption=_encryption())
        await sink.start()
        await sink.write_batch([_envelope(stream_sequence=1), _envelope(stream_sequence=2)])

    asyncio.run(_prepare())
    spool_config = tmp_path / "spool-config.json"
    target_config = tmp_path / "file-config.json"
    spool_config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "spool-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "spool",
                    "directory": str(tmp_path / "spool"),
                    "fsync": False,
                    "encryption": _encryption(),
                },
            }
        ),
        encoding="utf-8",
    )
    target_config.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "sink": {
                    "type": "file",
                    "directory": str(tmp_path / "events"),
                    "fsync": False,
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["replay-spool", str(spool_config), str(target_config), "--dry-run"],
    )

    assert result.exit_code == 0
    assert "2 committed spool record(s) eligible" in result.output


async def asyncio_to_thread(function: Callable[..., T], *args: object) -> T:
    """Run sync filesystem helpers away from async tests to satisfy Ruff."""

    return await asyncio.to_thread(function, *args)
