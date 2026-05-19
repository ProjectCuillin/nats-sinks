# SPDX-License-Identifier: Apache-2.0
"""Production local filesystem sink.

`FileSink` writes one JSON file per message under a configured directory.  It is
designed for local handoff, audit trails, simple archival flows, and development
pipelines where a durable filesystem path is the destination.

The sink treats file placement as the durable boundary:

1. Build a JSON document from the normalized `NatsEnvelope`.
2. Optionally gzip-compress the serialized JSON document using Python's
   standard-library `gzip` module.
3. Write the document to a temporary file in the destination directory.
4. Flush and optionally fsync the file.
5. Atomically place the file at its deterministic final path.
6. Optionally fsync the parent directory.

Only after all files in a batch complete those steps does `write_batch` return
success.  The core runner can then ACK the JetStream messages.
"""

from __future__ import annotations

import asyncio
import errno
import gzip
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import (
    ConfigurationError,
    DestinationUnavailableError,
    PermanentSinkError,
)
from nats_sinks.core.payload import PayloadStorageMode
from nats_sinks.file.config import (
    FileCompression,
    FileDuplicatePolicy,
    FileFilenameStrategy,
    FileSinkConfig,
    FileWriteMode,
)
from nats_sinks.file.mapping import file_record_for_envelope, relative_path_for_envelope


class FileSink:
    """Durably write JetStream messages to local JSON files.

    The direct constructor is intended for Python users.  The CLI uses
    `from_mapping` so the same Pydantic validation applies to JSON
    configuration files.
    """

    def __init__(
        self,
        *,
        directory: str | Path,
        mode: FileWriteMode = "one_file_per_message",
        filename_strategy: FileFilenameStrategy = "stream_sequence",
        duplicate_policy: FileDuplicatePolicy = "skip_existing",
        payload_mode: PayloadStorageMode = "json_or_envelope",
        extension: str | None = None,
        compression: FileCompression = "none",
        compression_level: int = 6,
        include_metadata: bool = True,
        partition_by_subject: bool = True,
        create_directory: bool = True,
        fsync: bool = True,
        pretty: bool = False,
    ) -> None:
        config_values: dict[str, Any] = {
            "directory": Path(directory),
            "mode": mode,
            "filename_strategy": filename_strategy,
            "duplicate_policy": duplicate_policy,
            "payload_mode": payload_mode,
            "compression": compression,
            "compression_level": compression_level,
            "include_metadata": include_metadata,
            "partition_by_subject": partition_by_subject,
            "create_directory": create_directory,
            "fsync": fsync,
            "pretty": pretty,
        }
        if extension is not None:
            config_values["extension"] = extension
        self.config = FileSinkConfig(**config_values)
        self._root: Path | None = None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> FileSink:
        """Create a file sink from raw sink configuration."""

        try:
            config = FileSinkConfig.model_validate(mapping)
        except PydanticValidationError as exc:
            raise ConfigurationError(str(exc)) from exc
        return cls(
            directory=config.directory,
            mode=config.mode,
            filename_strategy=config.filename_strategy,
            duplicate_policy=config.duplicate_policy,
            payload_mode=config.payload_mode,
            extension=config.extension,
            compression=config.compression,
            compression_level=config.compression_level,
            include_metadata=config.include_metadata,
            partition_by_subject=config.partition_by_subject,
            create_directory=config.create_directory,
            fsync=config.fsync,
            pretty=config.pretty,
        )

    async def start(self) -> None:
        """Prepare the destination directory before processing begins."""

        await asyncio.to_thread(self._prepare_directory)

    async def healthcheck(self) -> None:
        """Verify that the destination directory can accept an atomic write."""

        await asyncio.to_thread(self._healthcheck_sync)

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Write every message in the batch before returning success.

        File I/O is moved to a worker thread so a slow filesystem does not block
        the event loop that owns the NATS connection.
        """

        if not messages:
            return
        await asyncio.to_thread(self._write_batch_sync, list(messages))

    async def stop(self) -> None:
        """Release resources.

        The file sink does not keep open file descriptors between batches, so
        shutdown is intentionally a no-op.
        """

    def _prepare_directory(self) -> Path:
        raw_root = self.config.directory.expanduser()
        if raw_root.exists() and not raw_root.is_dir():
            raise ConfigurationError(f"file sink directory {raw_root} is not a directory")
        if not raw_root.exists():
            if not self.config.create_directory:
                raise ConfigurationError(f"file sink directory {raw_root} does not exist")
            try:
                raw_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DestinationUnavailableError(
                    f"failed to create file sink directory {raw_root}"
                ) from exc
        self._root = raw_root.resolve()
        return self._root

    def _root_dir(self) -> Path:
        return self._root or self._prepare_directory()

    def _safe_destination(self, relative_path: Path) -> Path:
        root = self._root_dir()
        destination = root / relative_path
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DestinationUnavailableError(
                f"failed to create file sink output directory {destination.parent}"
            ) from exc

        resolved_parent = destination.parent.resolve()
        resolved_destination = resolved_parent / destination.name
        try:
            resolved_destination.relative_to(root)
        except ValueError as exc:
            raise DestinationUnavailableError(
                "file sink destination resolved outside the configured directory"
            ) from exc
        return resolved_destination

    def _write_batch_sync(self, messages: Sequence[NatsEnvelope]) -> None:
        self._root_dir()
        for message in messages:
            record = file_record_for_envelope(message, config=self.config)
            destination = self._safe_destination(
                relative_path_for_envelope(message, config=self.config)
            )
            self._write_record(destination, record)

    def _json_bytes(self, record: dict[str, Any]) -> bytes:
        if self.config.pretty:
            rendered = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            rendered = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return f"{rendered}\n".encode()

    def _record_bytes(self, record: dict[str, Any]) -> bytes:
        """Serialize and optionally compress one file record.

        Compression is applied after JSON serialization and before the temporary
        file is flushed and atomically placed.  This keeps the durable boundary
        identical for compressed and uncompressed files.
        """

        payload = self._json_bytes(record)
        if self.config.compression == "none":
            return payload
        # Use the Python standard library rather than an operating-system gzip
        # executable.  That keeps the sink portable, avoids shell invocation,
        # and gives tests deterministic behavior across Linux, macOS, and CI.
        return gzip.compress(payload, compresslevel=self.config.compression_level, mtime=0)

    def _write_record(self, destination: Path, record: dict[str, Any]) -> None:
        if destination.exists() and self.config.duplicate_policy == "skip_existing":
            return
        if destination.exists() and self.config.duplicate_policy == "fail":
            raise PermanentSinkError(f"file sink destination already exists: {destination.name}")

        temp_path: Path | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(self._record_bytes(record))
                handle.flush()
                if self.config.fsync:
                    os.fsync(handle.fileno())

            self._commit_temp_file(temp_path, destination)
            if self.config.fsync:
                self._fsync_directory(destination.parent)
        except PermanentSinkError:
            raise
        except TypeError as exc:
            raise PermanentSinkError("file sink record is not JSON serializable") from exc
        except OSError as exc:
            raise DestinationUnavailableError(
                f"file sink failed to write destination file {destination.name}"
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _commit_temp_file(self, temp_path: Path, destination: Path) -> None:
        if self.config.duplicate_policy == "overwrite":
            os.replace(temp_path, destination)
            return

        try:
            os.link(temp_path, destination)
        except FileExistsError as exc:
            if self.config.duplicate_policy == "skip_existing":
                return
            raise PermanentSinkError(
                f"file sink destination already exists: {destination.name}"
            ) from exc
        except OSError as exc:
            # Some filesystems report EEXIST through a generic OSError.  Treat
            # that the same way as FileExistsError so redelivery remains safe.
            if exc.errno == errno.EEXIST and self.config.duplicate_policy == "skip_existing":
                return
            raise

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
                prefix=".nats-sinks-healthcheck.",
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
            raise DestinationUnavailableError("file sink healthcheck failed") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
