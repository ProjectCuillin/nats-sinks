# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Local load-test profiles for nats-sinks runtime behavior.

The profiles in this module are deliberately synthetic.  They do not connect
to NATS, Oracle, filesystems used by production sinks, or any private
infrastructure.  Instead, they exercise the framework's data-handling paths
with generated `NatsEnvelope` objects and produce sanitized timing summaries
that maintainers can use as repeatable local evidence.

These profiles are not a substitute for live throughput testing.  Their purpose
is to make normal, retry, DLQ, and shutdown pressure easy to rehearse without
secrets or services.  Reports include only counts and aggregate timings; they
never include raw payload bodies, hostnames, connection strings, usernames,
wallet paths, certificate material, or private operational subjects.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from nats_sinks.core.errors import SerializationError
from nats_sinks.core.metrics import (
    DEFAULT_METRIC_NAMESPACE,
    InMemoryMetrics,
    MetricNames,
    metrics_snapshot,
    write_metrics_snapshot,
)
from nats_sinks.core.payload import PayloadStorageMode
from nats_sinks.core.retry import RetryPolicy
from nats_sinks.testing.oracle_benchmark import sanitize_public_text
from nats_sinks.testing.synthetic import (
    SyntheticMessage,
    SyntheticScenarioProfile,
    generate_synthetic_scenario,
)

LoadProfileName = Literal["normal", "retry", "dlq", "shutdown"]
LoadProfileOutputFormat = Literal["json", "markdown"]

MAX_LOAD_PROFILE_MESSAGES = 100_000
MAX_LOAD_PROFILE_BATCH_SIZE = 10_000

_PHASE_ORDER: tuple[str, ...] = (
    "fetch",
    "payload_normalization",
    "metadata_resolution",
    "encryption",
    "backend_write",
    "commit",
    "ack",
    "retry",
    "dlq",
    "metrics_snapshot",
    "shutdown",
)


@dataclass(frozen=True, slots=True)
class LoadProfileOptions:
    """Validated knobs for a synthetic load-profile run.

    The options are public-safe by design.  They describe generated workload
    shape and local output behavior, not real service locations or
    credentials.
    """

    profile: LoadProfileName = "normal"
    message_count: int = 256
    batch_size: int = 64
    seed: int = 42
    encrypt_payloads: bool = False
    metrics_snapshot_file: Path | None = None
    preserve_metrics_snapshot: bool = False

    def __post_init__(self) -> None:
        """Reject unsupported or unbounded load-test options early."""

        if self.profile not in {"normal", "retry", "dlq", "shutdown"}:
            raise ValueError("load profile must be normal, retry, dlq, or shutdown")
        if self.message_count < 1 or self.message_count > MAX_LOAD_PROFILE_MESSAGES:
            raise ValueError(f"message_count must be between 1 and {MAX_LOAD_PROFILE_MESSAGES}")
        if self.batch_size < 1 or self.batch_size > MAX_LOAD_PROFILE_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_LOAD_PROFILE_BATCH_SIZE}")
        if self.seed < 0:
            raise ValueError("seed must not be negative")

    def public_dict(self) -> dict[str, Any]:
        """Return public-safe options for reports and issue evidence."""

        return {
            "profile": self.profile,
            "message_count": self.message_count,
            "batch_size": self.batch_size,
            "seed": self.seed,
            "encrypt_payloads": self.encrypt_payloads,
            "metrics_snapshot_written": self.metrics_snapshot_file is not None,
            "preserve_metrics_snapshot": self.preserve_metrics_snapshot,
        }


@dataclass(frozen=True, slots=True)
class LoadPhaseTiming:
    """Aggregate timing for one synthetic load-profile phase."""

    phase: str
    count: int
    total_seconds: float
    average_seconds: float
    max_seconds: float
    messages_per_second: float | None = None

    def __post_init__(self) -> None:
        """Reject non-finite timing values before public reports are rendered."""

        values = {
            "total_seconds": self.total_seconds,
            "average_seconds": self.average_seconds,
            "max_seconds": self.max_seconds,
        }
        if self.messages_per_second is not None:
            values["messages_per_second"] = self.messages_per_second
        for name, value in values.items():
            if not math.isfinite(value):
                raise ValueError(f"load-profile phase {self.phase!r} {name} must be finite")

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible phase summary."""

        data: dict[str, Any] = {
            "phase": self.phase,
            "count": self.count,
            "total_seconds": round(self.total_seconds, 6),
            "average_seconds": round(self.average_seconds, 6),
            "max_seconds": round(self.max_seconds, 6),
        }
        if self.messages_per_second is not None:
            data["messages_per_second"] = round(self.messages_per_second, 2)
        return data


@dataclass(frozen=True, slots=True)
class LoadProfileReport:
    """Public-safe result of one synthetic load-profile run."""

    options: LoadProfileOptions
    counters: dict[str, int]
    phases: tuple[LoadPhaseTiming, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)
    report_schema: str = "nats_sinks.testing.load_profile_report.v1"

    def to_dict(self) -> dict[str, Any]:
        """Render the report as JSON-compatible data."""

        return {
            "report_schema": self.report_schema,
            "scope": "sanitized-synthetic-load-profile",
            "options": self.options.public_dict(),
            "counters": dict(sorted(self.counters.items())),
            "phases": [phase.to_dict() for phase in self.phases],
            "notes": [sanitize_public_text(note) for note in self.notes],
        }


class _PhaseTimer:
    """Collect phase timings without exposing private workload details."""

    def __init__(self) -> None:
        self._values: dict[str, list[float]] = {phase: [] for phase in _PHASE_ORDER}

    def measure(self, phase: str) -> _MeasuredPhase:
        return _MeasuredPhase(self, phase)

    def add(self, phase: str, seconds: float) -> None:
        self._values.setdefault(phase, []).append(max(seconds, 0.0))

    def phases(self, *, phase_message_counts: Mapping[str, int]) -> tuple[LoadPhaseTiming, ...]:
        """Render timings using the completed-work count for each phase.

        Load profiles intentionally model partial-processing situations such as
        shutdown, DLQ routing, and retry pressure.  In those scenarios the
        number of generated messages is not the same as the number of messages
        handled by every phase.  Using phase-specific counters keeps public
        release evidence honest: a fetch phase reports fetched messages, a
        backend-write phase reports written records, and a DLQ phase reports
        messages actually routed to DLQ handling.
        """

        rendered: list[LoadPhaseTiming] = []
        for phase in _PHASE_ORDER:
            observations = self._values.get(phase, [])
            total = sum(observations)
            count = len(observations)
            average = total / count if count else 0.0
            max_value = max(observations) if observations else 0.0
            phase_message_count = max(phase_message_counts.get(phase, 0), 0)
            rate = phase_message_count / total if total > 0 else None
            rendered.append(
                LoadPhaseTiming(
                    phase=phase,
                    count=count,
                    total_seconds=total,
                    average_seconds=average,
                    max_seconds=max_value,
                    messages_per_second=rate,
                )
            )
        return tuple(rendered)


class _MeasuredPhase:
    """Context manager used by `_PhaseTimer`."""

    def __init__(self, timer: _PhaseTimer, phase: str) -> None:
        self._timer = timer
        self._phase = phase
        self._started = 0.0

    def __enter__(self) -> None:
        self._started = time.perf_counter()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self._timer.add(self._phase, time.perf_counter() - self._started)


def _batches(
    messages: Sequence[SyntheticMessage],
    *,
    batch_size: int,
) -> Iterable[list[SyntheticMessage]]:
    """Yield bounded batches from a deterministic message sequence."""

    for offset in range(0, len(messages), batch_size):
        yield list(messages[offset : offset + batch_size])


def _synthetic_encrypt_workload(messages: Sequence[SyntheticMessage]) -> int:
    """Perform deterministic local work that represents encryption pressure.

    The live encryption implementation depends on optional crypto libraries and
    non-deterministic nonces.  Load profiles avoid producing crypto material or
    ciphertext; this helper hashes generated payload bytes to model per-message
    CPU work and returns the number of messages that went through the synthetic
    encryption phase.
    """

    for message in messages:
        hashlib.sha256(message.envelope.data).hexdigest()
    return len(messages)


def _simulate_backend_write(records: Sequence[dict[str, Any]]) -> int:
    """Serialize records to model destination write pressure without a backend."""

    rendered = 0
    for record in records:
        rendered += len(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return rendered


def _record_metrics(metrics: InMemoryMetrics, counters: Counter[str]) -> None:
    """Populate a metrics snapshot from public-safe load-profile counters."""

    metrics.increment(MetricNames.MESSAGES_FETCHED_TOTAL, counters["messages_fetched"])
    metrics.increment(MetricNames.MESSAGES_PREPARED_TOTAL, counters["messages_prepared"])
    metrics.increment(MetricNames.MESSAGES_WRITTEN_TOTAL, counters["messages_written"])
    metrics.increment(MetricNames.MESSAGES_ACKED_TOTAL, counters["messages_acked"])
    metrics.increment(MetricNames.MESSAGES_NACKED_TOTAL, counters["messages_nacked"])
    metrics.increment(MetricNames.MESSAGES_FAILED_TOTAL, counters["messages_failed"])
    metrics.increment(MetricNames.MESSAGES_DLQ_TOTAL, counters["messages_dlq"])
    metrics.set_value(MetricNames.CURRENT_BATCH_MESSAGES, counters["last_batch_size"])


def _write_or_build_metrics_snapshot(
    *,
    metrics: InMemoryMetrics,
    options: LoadProfileOptions,
) -> None:
    """Exercise snapshot creation and optional atomic snapshot writing."""

    snapshot = metrics.snapshot()
    if options.metrics_snapshot_file is not None:
        write_metrics_snapshot(snapshot, options.metrics_snapshot_file)
        if not options.preserve_metrics_snapshot:
            options.metrics_snapshot_file.unlink(missing_ok=True)
    else:
        # Building the snapshot still exercises the serialization path without
        # leaving local files behind.
        metrics_snapshot(
            counters=dict(metrics.counters),
            gauges=dict(metrics.gauges),
            observations={name: list(values) for name, values in metrics.observations.items()},
            namespace=DEFAULT_METRIC_NAMESPACE,
        )


def _payload_storage_mode(
    *,
    options: LoadProfileOptions,
    message: SyntheticMessage,
) -> PayloadStorageMode:
    """Choose the payload mode used to model permanent DLQ failures."""

    if options.profile == "dlq" and message.malformed_json_text:
        return "json_only"
    return "json_or_envelope"


def _simulate_retry_pressure(
    *,
    options: LoadProfileOptions,
    batch_index: int,
    fetched_count: int,
    counters: Counter[str],
    timer: _PhaseTimer,
    retry_policy: RetryPolicy,
) -> None:
    """Model temporary sink pressure for retry-focused local profiles."""

    if options.profile != "retry" or batch_index % 2 != 0:
        return
    counters["retry_events"] += 1
    counters["messages_failed"] += fetched_count
    counters["messages_nacked"] += fetched_count
    timer.add("retry", retry_policy.backoff_seconds(counters["retry_events"]))


def _prepare_durable_records(
    *,
    options: LoadProfileOptions,
    fetched: Sequence[SyntheticMessage],
    timer: _PhaseTimer,
) -> tuple[list[dict[str, Any]], int]:
    """Normalize payloads into synthetic durable records and count DLQ records."""

    durable_records: list[dict[str, Any]] = []
    dlq_records = 0
    with timer.measure("payload_normalization"):
        for message in fetched:
            envelope = message.envelope
            try:
                payload = envelope.payload_for_json_storage(
                    mode=_payload_storage_mode(options=options, message=message)
                )
            except SerializationError:
                dlq_records += 1
                continue
            durable_records.append(
                {
                    "idempotency_key": envelope.idempotency_key(),
                    "payload_format": payload.original_format,
                    "payload_wrapped": payload.wrapped,
                    "payload_size_bytes": payload.size_bytes,
                }
            )
    return durable_records, dlq_records


def _phase_message_counts(
    *,
    options: LoadProfileOptions,
    counters: Counter[str],
) -> dict[str, int]:
    """Map each synthetic phase to the counter that represents its real work."""

    shutdown_count = counters["shutdown_unfetched_messages"] if options.profile == "shutdown" else 0
    return {
        "fetch": counters["messages_fetched"],
        "payload_normalization": counters["messages_prepared"],
        "metadata_resolution": counters["messages_prepared"],
        "encryption": counters["messages_encrypted"],
        "backend_write": counters["messages_written"],
        "commit": counters["messages_written"],
        "ack": counters["messages_acked"],
        "retry": counters["messages_nacked"],
        "dlq": counters["messages_dlq"],
        "metrics_snapshot": 1,
        "shutdown": shutdown_count,
    }


def _resolve_metadata(
    *,
    fetched: Sequence[SyntheticMessage],
    timer: _PhaseTimer,
) -> None:
    """Exercise generic NATS and mission metadata conversion paths."""

    with timer.measure("metadata_resolution"):
        for message in fetched:
            message.envelope.metadata_for_json_storage()
            message.envelope.mission_metadata_for_json_storage()


def _process_load_profile_batch(
    *,
    options: LoadProfileOptions,
    batch_index: int,
    batch: Sequence[SyntheticMessage],
    counters: Counter[str],
    timer: _PhaseTimer,
    retry_policy: RetryPolicy,
) -> None:
    """Process one bounded synthetic batch through the modeled runtime phases."""

    counters["last_batch_size"] = len(batch)
    with timer.measure("fetch"):
        fetched = list(batch)
    counters["messages_fetched"] += len(fetched)

    _simulate_retry_pressure(
        options=options,
        batch_index=batch_index,
        fetched_count=len(fetched),
        counters=counters,
        timer=timer,
        retry_policy=retry_policy,
    )

    durable_records, dlq_records = _prepare_durable_records(
        options=options,
        fetched=fetched,
        timer=timer,
    )
    counters["messages_dlq"] += dlq_records
    counters["messages_failed"] += dlq_records

    _resolve_metadata(fetched=fetched, timer=timer)
    counters["messages_prepared"] += len(fetched)

    if options.encrypt_payloads:
        with timer.measure("encryption"):
            counters["messages_encrypted"] += _synthetic_encrypt_workload(fetched)

    if dlq_records:
        with timer.measure("dlq"):
            counters["messages_acked"] += dlq_records

    with timer.measure("backend_write"):
        counters["backend_bytes_rendered"] += _simulate_backend_write(durable_records)
    counters["messages_written"] += len(durable_records)

    with timer.measure("commit"):
        # Commit is represented as a distinct local phase so reports can
        # compare backend serialization with the durable-boundary step.
        tuple(record["idempotency_key"] for record in durable_records)

    with timer.measure("ack"):
        counters["messages_acked"] += len(durable_records)


def run_load_profile(options: LoadProfileOptions) -> LoadProfileReport:
    """Run one synthetic load profile and return a sanitized report.

    The runner intentionally simulates delivery pressure instead of contacting
    live services.  This makes it safe for CI and public release evidence while
    still exercising the framework's normalization, metadata, retry, DLQ,
    metrics, and shutdown reporting paths.
    """

    profile = SyntheticScenarioProfile(
        name=f"load-{options.profile}",
        message_count=options.message_count,
        seed=options.seed,
    )
    messages = generate_synthetic_scenario(profile)
    timer = _PhaseTimer()
    metrics = InMemoryMetrics()
    counters: Counter[str] = Counter(
        {
            "messages_generated": len(messages),
            "messages_fetched": 0,
            "messages_prepared": 0,
            "messages_written": 0,
            "messages_acked": 0,
            "messages_nacked": 0,
            "messages_failed": 0,
            "messages_dlq": 0,
            "messages_encrypted": 0,
            "retry_events": 0,
            "backend_bytes_rendered": 0,
            "shutdown_unfetched_messages": 0,
            "last_batch_size": 0,
        }
    )
    retry_policy = RetryPolicy(
        max_retries=3,
        backoff_ms=100,
        backoff_mode="exponential",
        jitter="none",
    )
    all_batches = list(_batches(messages, batch_size=options.batch_size))
    max_batches = len(all_batches)
    if options.profile == "shutdown":
        max_batches = max(1, math.ceil(len(all_batches) / 2))
        counters["shutdown_unfetched_messages"] = sum(
            len(batch) for batch in all_batches[max_batches:]
        )

    for batch_index, batch in enumerate(all_batches[:max_batches], start=1):
        _process_load_profile_batch(
            options=options,
            batch_index=batch_index,
            batch=batch,
            counters=counters,
            timer=timer,
            retry_policy=retry_policy,
        )

    with timer.measure("shutdown"):
        if options.profile == "shutdown":
            # Simulate stop-fetch behavior: already fetched messages are
            # handled, unfetched messages remain outside the ACK boundary.
            tuple(
                message.envelope.subject for batch in all_batches[max_batches:] for message in batch
            )

    _record_metrics(metrics, counters)
    with timer.measure("metrics_snapshot"):
        _write_or_build_metrics_snapshot(metrics=metrics, options=options)

    notes = (
        "Load profiles are synthetic local observations, not portable throughput guarantees.",
        "Profiles do not contact live NATS, Oracle, file sinks, or observability services.",
        "Commit-then-ACK behavior is represented by separate write, commit, and ACK phases.",
    )
    return LoadProfileReport(
        options=options,
        counters=dict(counters),
        phases=timer.phases(
            phase_message_counts=_phase_message_counts(options=options, counters=counters)
        ),
        notes=notes,
    )


def render_load_profile_report_json(report: LoadProfileReport) -> str:
    """Render a load-profile report as stable JSON."""

    return json.dumps(report.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"


def render_load_profile_report_markdown(report: LoadProfileReport) -> str:
    """Render a load-profile report as Markdown suitable for issue evidence."""

    data = report.to_dict()
    lines = [
        "# Load Profile Report",
        "",
        "This report is sanitized. It contains generated workload counts and "
        "aggregate phase timings only. It does not include service endpoints, "
        "usernames, passwords, table names, wallet paths, certificates, private "
        "subjects, or payload bodies.",
        "",
        "## Options",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    for key, value in data["options"].items():
        lines.append(f"| `{key}` | `{sanitize_public_text(value)}` |")

    lines.extend(
        [
            "",
            "## Counters",
            "",
            "| Counter | Value |",
            "| --- | ---: |",
        ]
    )
    for key, value in data["counters"].items():
        lines.append(f"| `{key}` | {int(value)} |")

    lines.extend(
        [
            "",
            "## Phase Timings",
            "",
            "| Phase | Count | Total seconds | Average seconds | Max seconds | Messages/sec |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for phase in data["phases"]:
        rate = phase.get("messages_per_second")
        rate_text = f"{rate:.2f}" if isinstance(rate, float) else "n/a"
        lines.append(
            f"| {phase['phase']} | {phase['count']} | "
            f"{phase['total_seconds']:.6f} | {phase['average_seconds']:.6f} | "
            f"{phase['max_seconds']:.6f} | {rate_text} |"
        )

    if data["notes"]:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {sanitize_public_text(note)}" for note in data["notes"])
    lines.append("")
    return "\n".join(lines)


def render_load_profile_report(
    report: LoadProfileReport,
    *,
    output_format: LoadProfileOutputFormat,
) -> str:
    """Render a report as JSON or Markdown."""

    if output_format == "json":
        return render_load_profile_report_json(report)
    if output_format == "markdown":
        return render_load_profile_report_markdown(report)
    raise ValueError("unsupported load-profile report format")
