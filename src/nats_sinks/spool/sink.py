# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Encrypted local spool sink for disconnected edge operation.

`SpoolSink` lets an operator make local encrypted disk the durable destination
for a deployment phase.  The core runner still owns ACK decisions: it calls
`write_batch`, and it ACKs JetStream only after every record in the batch has
been committed to the spool directory.

Forwarding from the spool to a final destination is intentionally separate.
That separation keeps the ACK boundary explicit and makes disconnected
operation auditable: first commit to local custody, then replay later with
normal at-least-once and idempotency controls.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.encryption import PayloadEncryptor
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    NatsSinksError,
    PermanentSinkError,
    SerializationError,
)
from nats_sinks.sinks.base import Sink
from nats_sinks.spool.config import SpoolDrainOrdering, SpoolDuplicatePolicy, SpoolSinkConfig
from nats_sinks.spool.record import (
    build_plain_record,
    canonical_json_bytes,
    envelope_from_plain_record,
    spool_filename_for_envelope,
    unwrap_record,
    wrap_record,
)


@dataclass(frozen=True, slots=True)
class SpoolReplayResult:
    """Summary returned after replaying committed spool records."""

    scanned_records: int
    replayed_records: int
    deleted_records: int
    failed_records: int


@dataclass(frozen=True, slots=True)
class _SpoolEntry:
    """Internal replay plan item for one committed spool file."""

    path: Path
    priority_rank: int
    spooled_at_epoch_ns: int


class SpoolSink:
    """Durably write normalized messages to encrypted local spool files."""

    def __init__(
        self,
        *,
        directory: str | Path,
        max_records: int = 100_000,
        max_bytes: int = 10_737_418_240,
        duplicate_policy: SpoolDuplicatePolicy = "skip_existing",
        payload_mode: str = "json_or_envelope",
        include_metadata: bool = True,
        create_directory: bool = True,
        fsync: bool = True,
        pretty: bool = False,
        drain_ordering: SpoolDrainOrdering = "priority",
        delete_after_replay: bool = True,
        encryption: dict[str, Any] | None = None,
        allow_unencrypted: bool = False,
    ) -> None:
        config_values: dict[str, Any] = {
            "directory": Path(directory),
            "max_records": max_records,
            "max_bytes": max_bytes,
            "duplicate_policy": duplicate_policy,
            "payload_mode": payload_mode,
            "include_metadata": include_metadata,
            "create_directory": create_directory,
            "fsync": fsync,
            "pretty": pretty,
            "drain_ordering": drain_ordering,
            "delete_after_replay": delete_after_replay,
            "allow_unencrypted": allow_unencrypted,
        }
        if encryption is not None:
            config_values["encryption"] = encryption
        self.config = SpoolSinkConfig(**config_values)
        self._root: Path | None = None
        self._record_encryptor: PayloadEncryptor | None = None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> SpoolSink:
        """Create a spool sink from raw sink configuration."""

        try:
            config = SpoolSinkConfig.model_validate(mapping)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(
            directory=config.directory,
            max_records=config.max_records,
            max_bytes=config.max_bytes,
            duplicate_policy=config.duplicate_policy,
            payload_mode=config.payload_mode,
            include_metadata=config.include_metadata,
            create_directory=config.create_directory,
            fsync=config.fsync,
            pretty=config.pretty,
            drain_ordering=config.drain_ordering,
            delete_after_replay=config.delete_after_replay,
            encryption=config.encryption.model_dump(mode="python"),
            allow_unencrypted=config.allow_unencrypted,
        )

    async def start(self) -> None:
        """Prepare the spool directory before the runner starts fetching."""

        await asyncio.to_thread(self._prepare_directory)

    async def healthcheck(self) -> None:
        """Verify that the spool directory can accept an atomic write."""

        await asyncio.to_thread(self._healthcheck_sync)

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Commit every message in the batch to the local spool.

        A returned success means all non-duplicate records are atomically
        visible in the spool directory and, when `fsync` is enabled, their file
        content and parent directory entries have been flushed.  The core may
        ACK the corresponding JetStream messages only after this method returns.
        """

        if not messages:
            return
        await asyncio.to_thread(self._write_batch_sync, list(messages))

    async def stop(self) -> None:
        """Release resources.

        The spool sink opens files only for the duration of one atomic write, so
        shutdown is a no-op.
        """

    def _prepare_directory(self) -> Path:
        raw_root = self.config.directory.expanduser()
        if raw_root.exists() and not raw_root.is_dir():
            raise ConfigurationError(f"spool sink directory {raw_root} is not a directory")
        if not raw_root.exists():
            if not self.config.create_directory:
                raise ConfigurationError(f"spool sink directory {raw_root} does not exist")
            try:
                raw_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DestinationUnavailableError(
                    f"failed to create spool sink directory {raw_root}"
                ) from exc
        self._root = raw_root.resolve()
        return self._root

    def _root_dir(self) -> Path:
        return self._root or self._prepare_directory()

    def _write_batch_sync(self, messages: Sequence[NatsEnvelope]) -> None:
        root = self._root_dir()
        records: list[tuple[Path, dict[str, Any]]] = []
        planned_bytes = 0
        planned_records = 0
        for message in messages:
            destination = self._safe_destination(spool_filename_for_envelope(message))
            if destination.exists():
                if self.config.duplicate_policy == "skip_existing":
                    continue
                raise PermanentSinkError(
                    f"spool sink destination already exists: {destination.name}"
                )
            plain = build_plain_record(message, config=self.config)
            wrapper = wrap_record(plain, config=self.config, encryptor=self._encryptor())
            record_bytes = canonical_json_bytes(wrapper, pretty=self.config.pretty)
            records.append((destination, wrapper))
            planned_bytes += len(record_bytes)
            planned_records += 1

        if not records:
            return
        current_records, current_bytes = self._spool_usage(root)
        if current_records + planned_records > self.config.max_records:
            raise DestinationUnavailableError("spool sink record limit has been reached")
        if current_bytes + planned_bytes > self.config.max_bytes:
            raise DestinationUnavailableError("spool sink byte limit has been reached")

        for destination, wrapper in records:
            self._write_wrapper(destination, wrapper)

    def _safe_destination(self, filename: str) -> Path:
        root = self._root_dir()
        destination = root / filename
        resolved_destination = destination.resolve(strict=False)
        try:
            resolved_destination.relative_to(root)
        except ValueError as exc:
            raise DestinationUnavailableError(
                "spool sink destination resolved outside the configured directory"
            ) from exc
        return resolved_destination

    def _write_wrapper(self, destination: Path, wrapper: dict[str, Any]) -> None:
        temp_path: Path | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(canonical_json_bytes(wrapper, pretty=self.config.pretty))
                handle.flush()
                if self.config.fsync:
                    os.fsync(handle.fileno())
            self._commit_temp_file(temp_path, destination)
            if self.config.fsync:
                self._fsync_directory(destination.parent)
        except PermanentSinkError:
            raise
        except TypeError as exc:
            raise PermanentSinkError("spool sink record is not JSON serializable") from exc
        except OSError as exc:
            raise DestinationUnavailableError(
                f"spool sink failed to write destination file {destination.name}"
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _commit_temp_file(self, temp_path: Path, destination: Path) -> None:
        try:
            os.link(temp_path, destination)
        except FileExistsError as exc:
            if self.config.duplicate_policy == "skip_existing":
                return
            raise PermanentSinkError(
                f"spool sink destination already exists: {destination.name}"
            ) from exc
        except OSError as exc:
            if exc.errno == errno.EEXIST and self.config.duplicate_policy == "skip_existing":
                return
            raise

    def _spool_usage(self, root: Path) -> tuple[int, int]:
        records = 0
        total_bytes = 0
        for path in root.glob("*.spool.json"):
            if not path.is_file():
                continue
            records += 1
            try:
                total_bytes += path.stat().st_size
            except OSError as exc:
                raise DestinationUnavailableError("failed to inspect spool usage") from exc
        return records, total_bytes

    def _encryptor(self) -> PayloadEncryptor | None:
        """Return the cached record encryptor when record encryption is enabled."""

        if not self.config.encryption.enabled:
            return None
        if self._record_encryptor is None:
            self._record_encryptor = PayloadEncryptor(self.config.encryption)
        return self._record_encryptor

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _healthcheck_sync(self) -> None:
        root = self._root_dir()
        temp_path: Path | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=".nats-sinks-spool-healthcheck.",
                suffix=".tmp",
                dir=root,
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(b"")
                handle.flush()
                if self.config.fsync:
                    os.fsync(handle.fileno())
            if self.config.fsync:
                self._fsync_directory(root)
        except OSError as exc:
            raise DestinationUnavailableError("spool sink healthcheck failed") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def committed_entries(self) -> list[_SpoolEntry]:
        """Return committed records in the configured replay order."""

        root = self._root_dir()
        entries: list[_SpoolEntry] = []
        for path in root.glob("*.spool.json"):
            if not path.is_file():
                continue
            wrapper = _load_wrapper(path)
            rank = wrapper.get("priority_rank")
            spooled_at = wrapper.get("spooled_at_epoch_ns")
            entries.append(
                _SpoolEntry(
                    path=path,
                    priority_rank=rank if isinstance(rank, int) else 999,
                    spooled_at_epoch_ns=spooled_at if isinstance(spooled_at, int) else 0,
                )
            )
        if self.config.drain_ordering == "priority":
            return sorted(entries, key=lambda item: (item.priority_rank, item.spooled_at_epoch_ns))
        return sorted(entries, key=lambda item: (item.spooled_at_epoch_ns, item.path.name))

    def load_envelope(self, path: Path) -> NatsEnvelope:
        """Load and decrypt one committed spool record for replay."""

        wrapper = _load_wrapper(path)
        record = unwrap_record(wrapper, encryption=self.config.encryption)
        return envelope_from_plain_record(record)


async def replay_spool_to_sink(
    spool: SpoolSink,
    sink: Sink,
    *,
    max_records: int | None = None,
) -> SpoolReplayResult:
    """Replay committed spool files into another sink.

    The target sink is responsible for its own durable commit.  A spool file is
    deleted only after `sink.write_batch([envelope])` returns success and the
    spool configuration allows cleanup.  If the target raises, replay stops and
    the file remains eligible for another attempt.
    """

    entries = await asyncio.to_thread(spool.committed_entries)
    if max_records is not None:
        entries = entries[:max_records]

    replayed = 0
    deleted = 0
    failed = 0
    for entry in entries:
        try:
            envelope = await asyncio.to_thread(spool.load_envelope, entry.path)
            await sink.write_batch([envelope])
            replayed += 1
            if spool.config.delete_after_replay:
                await asyncio.to_thread(entry.path.unlink)
                deleted += 1
        except NatsSinksError:
            failed += 1
            raise
    return SpoolReplayResult(
        scanned_records=len(entries),
        replayed_records=replayed,
        deleted_records=deleted,
        failed_records=failed,
    )


def _load_wrapper(path: Path) -> dict[str, Any]:
    """Load one spool wrapper file without exposing its contents in errors."""

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SerializationError(f"spool file {path.name} is not a valid wrapper") from exc
    if not isinstance(loaded, dict):
        raise SerializationError(f"spool file {path.name} wrapper must be a JSON object")
    return loaded
