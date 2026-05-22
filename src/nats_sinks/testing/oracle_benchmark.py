# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Sanitized Oracle benchmark report helpers.

The live Oracle benchmark script uses this module to turn phase timings into a
public-safe report.  Keeping the redaction and rendering logic in importable
Python makes the important safety behavior unit-testable without requiring
NATS, Oracle, wallets, certificates, or credentials in CI.

Benchmark reports intentionally summarize only configuration knobs and timing
aggregates.  They do not include server locations, database usernames, table
names, connection strings, wallet paths, certificate material, or payload
samples.  Operators can copy the rendered output into public issue comments or
`docs/test-report.md` without disclosing private environment details.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from nats_sinks.core.metrics import InMemoryMetrics, MetricNames

BenchmarkPayloadShape = Literal["json", "text", "mixed", "empty", "binary"]
BenchmarkOutputFormat = Literal["json", "markdown"]
BenchmarkSinkMode = Literal["merge", "insert_ignore", "insert", "append"]

MAX_BENCHMARK_MESSAGES = 1_000_000
MAX_BENCHMARK_BATCH_SIZE = 10_000

_PHASE_TO_METRIC: dict[str, str] = {
    "fetch": MetricNames.NATS_FETCH_SECONDS,
    "map": MetricNames.MESSAGE_MAPPING_SECONDS,
    "write": MetricNames.ORACLE_EXECUTE_SECONDS,
    "commit": MetricNames.ORACLE_COMMIT_SECONDS,
    "ack": MetricNames.MESSAGE_ACK_SECONDS,
    "retry": MetricNames.RETRY_BACKOFF_DELAY_SECONDS,
}

_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|credential|credentials|private[_-]?key|"
    r"wallet|dsn|connect[_-]?string|user|username)\b\s*[:=]\s*[^,\s;]+"
)
_URL_RE = re.compile(r"(?i)\b(?:nats|tls|tcps|http|https)://[^\s,)]+")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_ORACLE_DESCRIPTOR_RE = re.compile(r"(?i)\(description=.*\)")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True, slots=True)
class OracleBenchmarkOptions:
    """Validated public knobs for one Oracle benchmark run.

    The options intentionally exclude private locators and credentials.  Live
    connection details are read by the script from ignored local environment
    files, while reports keep only operationally useful knobs such as message
    count, batch size, payload shape, encryption mode, and sink write mode.
    """

    message_count: int = 256
    batch_size: int = 64
    payload_shape: BenchmarkPayloadShape = "mixed"
    sink_mode: BenchmarkSinkMode = "merge"
    encryption_enabled: bool = False
    encryption_algorithm: str = "none"
    drop_table_before: bool = False
    drop_table_after: bool = False

    def __post_init__(self) -> None:
        """Reject unbounded or unsupported benchmark options."""

        if self.message_count < 1 or self.message_count > MAX_BENCHMARK_MESSAGES:
            raise ValueError(f"message_count must be between 1 and {MAX_BENCHMARK_MESSAGES}")
        if self.batch_size < 1 or self.batch_size > MAX_BENCHMARK_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_BENCHMARK_BATCH_SIZE}")
        if self.payload_shape not in {"json", "text", "mixed", "empty", "binary"}:
            raise ValueError("payload_shape is not supported")
        if self.sink_mode not in {"merge", "insert_ignore", "insert", "append"}:
            raise ValueError("sink_mode is not supported")
        if self.encryption_enabled and self.encryption_algorithm not in {
            "aes-256-gcm",
            "aes-256-ccm",
        }:
            raise ValueError("encryption_algorithm must be aes-256-gcm or aes-256-ccm")

    def to_public_dict(self) -> dict[str, Any]:
        """Return report-safe benchmark options."""

        return {
            "message_count": self.message_count,
            "batch_size": self.batch_size,
            "payload_shape": self.payload_shape,
            "sink_mode": self.sink_mode,
            "encryption_enabled": self.encryption_enabled,
            "encryption_algorithm": self.encryption_algorithm
            if self.encryption_enabled
            else "none",
            "drop_table_before": self.drop_table_before,
            "drop_table_after": self.drop_table_after,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkPhaseTiming:
    """Aggregate timing for one benchmark phase."""

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
                raise ValueError(f"Oracle benchmark phase {self.phase!r} {name} must be finite")

    def to_dict(self) -> dict[str, Any]:
        """Render the phase as JSON-serializable data with stable precision."""

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
class OracleBenchmarkReport:
    """Public-safe Oracle benchmark report."""

    options: OracleBenchmarkOptions
    phases: tuple[BenchmarkPhaseTiming, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)
    report_schema: str = "nats_sinks.testing.oracle_benchmark_report.v1"

    def to_dict(self) -> dict[str, Any]:
        """Render the report as sanitized JSON-serializable data."""

        return {
            "report_schema": self.report_schema,
            "scope": "sanitized-nats-to-oracle-benchmark",
            "options": self.options.to_public_dict(),
            "phases": [phase.to_dict() for phase in self.phases],
            "notes": [sanitize_public_text(note) for note in self.notes],
        }


def sanitize_public_text(value: object) -> str:
    """Return text safe for public benchmark output.

    This redactor is intentionally conservative.  It removes common locators,
    credential assignments, Oracle descriptors, and control characters.  The
    benchmark report avoids including such values in the first place, but this
    final pass protects comments and test reports from accidental leakage.
    """

    try:
        rendered = str(value)
    except Exception:
        rendered = "<unrenderable>"
    rendered = _CONTROL_RE.sub(" ", rendered)
    rendered = _ORACLE_DESCRIPTOR_RE.sub("<redacted-oracle-descriptor>", rendered)
    rendered = _URL_RE.sub("<redacted-url>", rendered)
    rendered = _IPV4_RE.sub("<redacted-ip>", rendered)
    rendered = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        rendered,
    )
    return rendered.strip()


def _phase_from_observations(
    *,
    phase: str,
    observations: list[float],
    message_count: int | None,
) -> BenchmarkPhaseTiming:
    """Build one phase summary from observed durations."""

    total = sum(observations)
    count = len(observations)
    average = total / count if count else 0.0
    max_value = max(observations) if observations else 0.0
    rate = message_count / total if message_count is not None and total > 0 else None
    return BenchmarkPhaseTiming(
        phase=phase,
        count=count,
        total_seconds=total,
        average_seconds=average,
        max_seconds=max_value,
        messages_per_second=rate,
    )


def build_oracle_benchmark_report(
    *,
    options: OracleBenchmarkOptions,
    metrics: InMemoryMetrics,
    explicit_phase_seconds: Mapping[str, list[float]] | None = None,
    notes: tuple[str, ...] = (),
) -> OracleBenchmarkReport:
    """Build a sanitized benchmark report from metrics and explicit timings."""

    phase_seconds = {key: list(value) for key, value in (explicit_phase_seconds or {}).items()}
    phases: list[BenchmarkPhaseTiming] = []
    ordered_phase_names = (
        "publish",
        "fetch",
        "map",
        "write",
        "commit",
        "ack",
        "retry",
        "shutdown",
    )
    throughput_phase_counts = {
        # These phases represent work over the configured benchmark message
        # count. Retry-delay and shutdown are lifecycle timing observations and
        # intentionally omit messages-per-second until a future benchmark
        # records explicit phase-specific work counts for them.
        "publish": options.message_count,
        "fetch": options.message_count,
        "map": options.message_count,
        "write": options.message_count,
        "commit": options.message_count,
        "ack": options.message_count,
    }
    for phase in ordered_phase_names:
        metric_name = _PHASE_TO_METRIC.get(phase)
        observations = phase_seconds.get(phase)
        if observations is None and metric_name is not None:
            observations = list(metrics.observations.get(metric_name, []))
        phases.append(
            _phase_from_observations(
                phase=phase,
                observations=observations or [],
                message_count=throughput_phase_counts.get(phase),
            )
        )
    return OracleBenchmarkReport(options=options, phases=tuple(phases), notes=notes)


def render_oracle_benchmark_report_json(report: OracleBenchmarkReport) -> str:
    """Render a report as stable JSON."""

    return json.dumps(report.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"


def render_oracle_benchmark_report_markdown(report: OracleBenchmarkReport) -> str:
    """Render a report as Markdown suitable for issue evidence."""

    data = report.to_dict()
    options = data["options"]
    lines = [
        "# Oracle Benchmark Report",
        "",
        "This report is sanitized. It contains timing observations only and does "
        "not include server addresses, usernames, passwords, table names, wallet "
        "paths, certificates, connection strings, or payload bodies.",
        "",
        "## Options",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    for key, value in options.items():
        lines.append(f"| `{key}` | `{sanitize_public_text(value)}` |")
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


def render_oracle_benchmark_report(
    report: OracleBenchmarkReport,
    *,
    output_format: BenchmarkOutputFormat,
) -> str:
    """Render a report in the requested public-safe format."""

    if output_format == "json":
        return render_oracle_benchmark_report_json(report)
    if output_format == "markdown":
        return render_oracle_benchmark_report_markdown(report)
    raise ValueError("unsupported benchmark report format")
