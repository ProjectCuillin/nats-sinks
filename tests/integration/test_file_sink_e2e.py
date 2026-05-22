# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import gzip
import json
import os
import secrets
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from nats_sinks.core.config import EncryptionConfig, EncryptionRuleConfig, MessageMetadataConfig
from nats_sinks.core.encryption import ENCRYPTED_PAYLOAD_KEY, PayloadEncryptor
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
    def __init__(
        self,
        events: list[str],
        *,
        sequence: int,
        data: bytes,
        subject: str = "orders.created",
    ) -> None:
        self.subject = subject
        self.data = data
        self.headers = {"Nats-Msg-Id": f"file-e2e-{sequence}"}
        if sequence == 1:
            self.headers["Nats-Sinks-Priority"] = "urgent"
            self.headers["Nats-Sinks-Classification"] = "restricted"
            self.headers["Nats-Sinks-Labels"] = "billing;customer-facing"
        elif sequence == 2:
            self.headers["Nats-Sinks-Priority"] = "normal"
            self.headers["Nats-Sinks-Labels"] = "standard"
        elif sequence == 3:
            self.headers["Nats-Sinks-Classification"] = "internal"
        else:
            self.headers["Nats-Sinks-Priority"] = ""
            self.headers["Nats-Sinks-Labels"] = ""
        self.metadata = FakeMetadata(sequence=FakeSequence(stream=sequence, consumer=sequence))
        self.events = events
        self.acked = False

    async def ack(self) -> None:
        self.events.append(f"ack-{self.metadata.sequence.stream}")
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        del delay
        self.events.append(f"nak-{self.metadata.sequence.stream}")


class MetadataDefaultMessage:
    """Raw-message double without metadata headers, used to test defaults."""

    def __init__(self, events: list[str], *, sequence: int, subject: str) -> None:
        self.subject = subject
        self.data = b"{}"
        self.headers = {"Nats-Msg-Id": f"metadata-default-{sequence}"}
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


def _encryption_config() -> EncryptionConfig:
    configured = os.getenv("NATS_SINKS_TEST_ENCRYPTION_KEY_B64")
    key_b64 = configured or base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return EncryptionConfig(
        enabled=True,
        algorithm="aes-256-gcm",
        key_id="file-e2e-test-key",
        key_b64=key_b64,
    )


async def _run_file_sink_e2e(
    *,
    output_dir: Path,
    compression: str = "none",
    encryption: EncryptionConfig | None = None,
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
        encryption=encryption,
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
        assert records[0]["priority"] == "urgent"
        assert records[0]["classification"] == "restricted"
        assert records[0]["labels"] == "billing;customer-facing"
        assert records[0]["labels_list"] == ["billing", "customer-facing"]
        assert records[1]["priority"] == "normal"
        assert records[1]["classification"] is None
        assert records[1]["labels"] == "standard"
        assert records[2]["priority"] is None
        assert records[2]["classification"] == "internal"
        assert records[2]["labels"] is None
        assert records[3]["priority"] is None
        assert records[3]["classification"] is None
        assert records[3]["labels"] is None
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
        assert records[0]["metadata"]["message_metadata"] == {
            "priority": "urgent",
            "classification": "restricted",
            "labels": ["billing", "customer-facing"],
        }
        assert records[1]["metadata"]["message_metadata"] == {
            "priority": "normal",
            "classification": None,
            "labels": ["standard"],
        }
        assert all(record["metadata"]["jetstream"]["stream"] == "ORDERS" for record in records)
    finally:
        _cleanup_file_e2e_directory(output_dir, tmp_path=tmp_path)


async def test_runner_file_sink_local_end_to_end_with_payload_encryption(tmp_path: Path) -> None:
    """Prove encrypted core payloads stay decryptable after file sink durability."""

    output_dir = _file_e2e_directory(tmp_path, compression="encrypted")
    encryption = _encryption_config()
    encryptor = PayloadEncryptor(encryption)
    try:
        files, records, events = await _run_file_sink_e2e(
            output_dir=output_dir,
            encryption=encryption,
        )

        assert events == ["ack-1", "ack-2", "ack-3", "ack-4"]
        assert len(files) == 4
        for record in records:
            assert ENCRYPTED_PAYLOAD_KEY in record["payload"]
        assert encryptor.decrypt_payload(records[0]["payload"]) == b'{"order_id":"O-1001"}'
        assert encryptor.decrypt_payload(records[1]["payload"]) == b"encrypted-text"
        assert encryptor.decrypt_payload(records[2]["payload"]) == b""
        assert encryptor.decrypt_payload(records[3]["payload"]) == b"\xff\x00\xfe"
        assert records[0]["priority"] == "urgent"
        assert records[1]["classification"] is None
        assert all(record["metadata"]["jetstream"]["stream"] == "ORDERS" for record in records)
    finally:
        _cleanup_file_e2e_directory(output_dir, tmp_path=tmp_path)


async def test_runner_file_sink_local_end_to_end_with_subject_payload_encryption(
    tmp_path: Path,
) -> None:
    """Prove subject rules can encrypt one subject family while leaving another clear."""

    output_dir = _file_e2e_directory(tmp_path, compression="subject-encryption")
    key_b64 = _encryption_config().key_b64
    assert key_b64 is not None
    encryption = EncryptionConfig(
        enabled=False,
        rules=[
            EncryptionRuleConfig(
                subject="secure.>",
                enabled=True,
                key_id="file-subject-e2e-key",
                key_b64=key_b64,
            )
        ],
    )
    encryptor = PayloadEncryptor(encryption.effective_rule_config(encryption.rules[0]))
    events: list[str] = []
    messages: Sequence[FakeMessage] = [
        FakeMessage(events, sequence=1, subject="secure.orders", data=b"secret-orders"),
        FakeMessage(events, sequence=2, subject="public.orders", data=b"public-orders"),
    ]
    sink = FileSink(directory=output_dir, fsync=False)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="file-orders-sink",
        subject=">",
        sink=sink,
        encryption=encryption,
    )

    try:
        await sink.start()
        await runner.process_raw_batch(messages)

        records = _sort_records_by_subject(
            [_read_file_record(path) for path in _json_files(output_dir)]
        )
        public_record, secure_record = records
        assert public_record["subject"] == "public.orders"
        assert secure_record["subject"] == "secure.orders"
        assert public_record["payload"]["_nats_sinks"]["payload_format"] == "text"
        assert ENCRYPTED_PAYLOAD_KEY in secure_record["payload"]
        assert encryptor.decrypt_payload(secure_record["payload"]) == b"secret-orders"
        assert events == ["ack-1", "ack-2"]
    finally:
        _cleanup_file_e2e_directory(output_dir, tmp_path=tmp_path)


async def test_runner_file_sink_local_end_to_end_with_subject_metadata_defaults(
    tmp_path: Path,
) -> None:
    """Prove subject-specific metadata defaults are persisted by a production sink."""

    output_dir = _file_e2e_directory(tmp_path, compression="subject-metadata")
    events: list[str] = []
    messages: Sequence[MetadataDefaultMessage] = [
        MetadataDefaultMessage(events, sequence=1, subject="orders.urgent.created"),
        MetadataDefaultMessage(events, sequence=2, subject="public.status"),
        MetadataDefaultMessage(events, sequence=3, subject="orders.created"),
    ]
    metadata = MessageMetadataConfig.model_validate(
        {
            "priority": {
                "header": "X-Priority",
                "default": "normal",
            },
            "classification": {
                "header": "X-Classification",
                "default": "internal",
            },
            "labels": {
                "header": "X-Labels",
                "default": "default;orders",
            },
            "rules": [
                {
                    "subject": "orders.urgent.>",
                    "priority": "urgent",
                    "classification": "restricted",
                    "labels": "urgent;customer-facing",
                },
                {
                    "subject": "public.>",
                    "priority": "low",
                    "classification": None,
                    "labels": None,
                },
            ],
        }
    )
    sink = FileSink(directory=output_dir, fsync=False)
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="file-orders-sink",
        subject=">",
        sink=sink,
        message_metadata=metadata,
    )

    try:
        await sink.start()
        await runner.process_raw_batch(messages)

        records = _sort_records_by_subject(
            [_read_file_record(path) for path in _json_files(output_dir)]
        )
        orders_record, urgent_record, public_record = records
        assert orders_record["subject"] == "orders.created"
        assert orders_record["priority"] == "normal"
        assert orders_record["classification"] == "internal"
        assert orders_record["labels"] == "default;orders"
        assert public_record["subject"] == "public.status"
        assert public_record["priority"] == "low"
        assert public_record["classification"] is None
        assert public_record["labels"] is None
        assert urgent_record["subject"] == "orders.urgent.created"
        assert urgent_record["priority"] == "urgent"
        assert urgent_record["classification"] == "restricted"
        assert urgent_record["labels"] == "urgent;customer-facing"
        assert events == ["ack-1", "ack-2", "ack-3"]
    finally:
        _cleanup_file_e2e_directory(output_dir, tmp_path=tmp_path)


def _sort_records_by_subject(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Sort file records by subject so assertions do not depend on filename order."""

    return sorted(records, key=lambda record: str(record["subject"]))
