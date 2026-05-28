# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Deterministic multi-sink routing certification helpers.

The helpers in this module exercise the production fan-out router with local
file-backed probe sinks. They intentionally avoid live backends by default so
route selection, optional sink waits, required sink failures, duplicate
redelivery, and no-route handling can be validated during ordinary local
checks without credentials or network access.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from nats_sinks.core.config import AppConfig, RoutingMatchPolicyConfig, load_config
from nats_sinks.core.envelope import NatsEnvelope
from nats_sinks.core.errors import PermanentSinkError, TemporarySinkError
from nats_sinks.core.fanout_sink import FanoutSink
from nats_sinks.core.routing_policy import select_route_targets

MULTI_SINK_ROUTING_SCHEMA_VERSION = 1
MULTI_SINK_ROUTING_FLOW_HEADER = "Nats-Sinks-Flow"
MULTI_SINK_ROUTING_FLOW_VALUE = "multi-sink-routing-e2e"
MULTI_SINK_EXAMPLE_CONFIG = Path("examples/multi-sink-routing-e2e/config.json")

_SAFE_FILE_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_SUBJECT_FAMILY_MIN_TOKENS = 2


class MultiSinkRoutingCertificationError(RuntimeError):
    """Raised when deterministic multi-sink route certification fails."""


@dataclass(frozen=True)
class ReducedSinkRecord:
    """Sanitized evidence for one routed message written by one probe sink."""

    sink: str
    sink_type: str
    message_id: str
    subject_family: str
    priority: str | None
    classification: str | None
    labels: tuple[str, ...]
    route_header: str | None
    stream_sequence: int | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation without payloads or file paths."""

        return {
            "sink": self.sink,
            "sink_type": self.sink_type,
            "message_id": self.message_id,
            "subject_family": self.subject_family,
            "priority": self.priority,
            "classification": self.classification,
            "labels": list(self.labels),
            "route_header": self.route_header,
            "stream_sequence": self.stream_sequence,
        }


@dataclass(frozen=True)
class MultiSinkRoutingReport:
    """Sanitized result from a deterministic multi-sink routing run."""

    schema_version: int
    mode: Literal["reduced"]
    config_validated: bool
    route_matrix: list[dict[str, Any]]
    expected_by_sink: dict[str, list[str]]
    actual_by_sink: dict[str, list[str]]
    duplicate_attempts_by_sink: dict[str, int]
    evidence_file_counts_by_sink: dict[str, int]
    no_route_message_ids: list[str]
    optional_timeout_observed: bool
    required_failure_blocked_ack: bool
    reject_no_route_observed: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, sanitized JSON-safe report."""

        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "config_validated": self.config_validated,
            "route_matrix": self.route_matrix,
            "expected_by_sink": self.expected_by_sink,
            "actual_by_sink": self.actual_by_sink,
            "duplicate_attempts_by_sink": self.duplicate_attempts_by_sink,
            "evidence_file_counts_by_sink": self.evidence_file_counts_by_sink,
            "no_route_message_ids": self.no_route_message_ids,
            "optional_timeout_observed": self.optional_timeout_observed,
            "required_failure_blocked_ack": self.required_failure_blocked_ack,
            "reject_no_route_observed": self.reject_no_route_observed,
        }

    def to_json(self) -> str:
        """Return pretty JSON suitable for local reports and shell piping."""

        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


class ReducedBackendSink:
    """Local probe sink used by the reduced multi-sink routing flow.

    The probe writes one sanitized JSON evidence file per unique message ID and
    sink. It never stores payload bytes and treats repeated message IDs as
    duplicate redelivery attempts.
    """

    def __init__(
        self,
        *,
        name: str,
        sink_type: str,
        evidence_dir: Path,
        fail: bool = False,
        hang: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        self.name = name
        self.sink_type = sink_type
        self.evidence_dir = evidence_dir
        self.fail = fail
        self.hang = hang
        self.delay_seconds = delay_seconds
        self.records: list[ReducedSinkRecord] = []
        self.duplicate_attempts = 0
        self._seen_message_ids: set[str] = set()

    async def start(self) -> None:
        """Create the local evidence directory."""

        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    async def stop(self) -> None:
        """Probe sinks do not hold external resources."""

    async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
        """Record routed messages or simulate configured failure modes."""

        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail:
            raise RuntimeError(f"reduced backend sink {self.name} failed")
        for message in messages:
            record = _record_for_message(
                sink=self.name,
                sink_type=self.sink_type,
                message=message,
            )
            if record.message_id in self._seen_message_ids:
                self.duplicate_attempts += 1
                continue
            self._seen_message_ids.add(record.message_id)
            self.records.append(record)
            self._write_record(record)

    @property
    def message_ids(self) -> list[str]:
        """Return message IDs committed by this probe sink."""

        return [record.message_id for record in self.records]

    @property
    def evidence_file_count(self) -> int:
        """Return the number of sanitized evidence files for this sink."""

        return len(list(self.evidence_dir.glob("*.json")))

    def _write_record(self, record: ReducedSinkRecord) -> None:
        safe_name = _SAFE_FILE_ID_RE.sub("_", record.message_id)
        target = self.evidence_dir / f"{safe_name}.json"
        target.write_text(
            json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def run_reduced_multi_sink_routing_flow_sync(
    *,
    work_dir: Path,
    config_path: Path = MULTI_SINK_EXAMPLE_CONFIG,
) -> MultiSinkRoutingReport:
    """Run the reduced multi-sink routing flow from synchronous callers."""

    return asyncio.run(
        run_reduced_multi_sink_routing_flow(
            work_dir=work_dir,
            config_path=config_path,
        )
    )


async def run_reduced_multi_sink_routing_flow(
    *,
    work_dir: Path,
    config_path: Path = MULTI_SINK_EXAMPLE_CONFIG,
) -> MultiSinkRoutingReport:
    """Run deterministic fan-out routing checks with local probe sinks."""

    config = load_config(config_path)
    if config.routing is None:
        raise MultiSinkRoutingCertificationError("multi-sink config has no routing")

    work_dir.mkdir(parents=True, exist_ok=True)
    children = _build_reduced_children(config, work_dir / "success")
    fanout = FanoutSink(children=children, routing=config.routing)
    envelopes = multi_sink_routing_envelopes()

    await fanout.start()
    try:
        await fanout.write_batch(envelopes)
        expected_by_sink, no_route_message_ids = _expected_routing(
            routing=config.routing,
            envelopes=envelopes,
        )
        _assert_expected_messages(children, expected_by_sink)

        # Replaying the same messages proves duplicate redelivery is safe in the
        # reduced backend probes and does not create duplicate committed records.
        await fanout.write_batch(envelopes)
        _assert_expected_messages(children, expected_by_sink)
    finally:
        await fanout.stop()

    optional_timeout_observed = await _exercise_optional_timeout(config, work_dir)
    required_failure_blocked_ack = await _exercise_required_failure(config, work_dir)
    reject_no_route_observed = await _exercise_reject_no_route(config, work_dir)

    return MultiSinkRoutingReport(
        schema_version=MULTI_SINK_ROUTING_SCHEMA_VERSION,
        mode="reduced",
        config_validated=True,
        route_matrix=_route_matrix(config),
        expected_by_sink=expected_by_sink,
        actual_by_sink=_actual_by_sink(children),
        duplicate_attempts_by_sink={
            name: child.duplicate_attempts for name, child in sorted(children.items())
        },
        evidence_file_counts_by_sink={
            name: child.evidence_file_count for name, child in sorted(children.items())
        },
        no_route_message_ids=no_route_message_ids,
        optional_timeout_observed=optional_timeout_observed,
        required_failure_blocked_ack=required_failure_blocked_ack,
        reject_no_route_observed=reject_no_route_observed,
    )


def multi_sink_routing_envelopes() -> list[NatsEnvelope]:
    """Return fake, sanitized envelopes for the multi-sink routing matrix."""

    return [
        _envelope(
            message_id="MSG-SECRET-1",
            subject="mission.sensor.alpha",
            priority="urgent",
            classification="NATO SECRET",
            labels=("sensor", "audit", "edge"),
            route_header="mission-audit",
            stream_sequence=1,
        ),
        _envelope(
            message_id="MSG-UNCLASS-1",
            subject="mission.sensor.alpha",
            priority="urgent",
            classification="NATO UNCLASS",
            labels=("sensor", "audit", "coalition"),
            route_header="unclass-audit",
            stream_sequence=2,
        ),
        _envelope(
            message_id="MSG-TASKING-1",
            subject="mission.tasking.alpha",
            priority="normal",
            classification="NATO RESTRICTED",
            labels=("tasking", "read-model"),
            route_header="tasking-cache",
            stream_sequence=3,
        ),
        _envelope(
            message_id="MSG-NO-ROUTE-1",
            subject="mission.weather.alpha",
            priority="low",
            classification="NATO UNCLASS",
            labels=("weather",),
            route_header="weather",
            stream_sequence=4,
        ),
        _envelope(
            message_id="MSG-TRAINING-1",
            subject="mission.sensor.alpha",
            priority="urgent",
            classification="NATO SECRET",
            labels=("sensor", "audit", "training"),
            route_header="mission-audit",
            stream_sequence=5,
        ),
    ]


def write_report(report: MultiSinkRoutingReport, path: Path) -> None:
    """Write a sanitized report as deterministic JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_json() + "\n", encoding="utf-8")


def _build_reduced_children(
    config: AppConfig,
    evidence_root: Path,
    *,
    fail_sink: str | None = None,
    hang_sink: str | None = None,
    fail_delay_seconds: float = 0.0,
) -> dict[str, ReducedBackendSink]:
    if config.routing is None:
        raise MultiSinkRoutingCertificationError("multi-sink config has no routing")
    children: dict[str, ReducedBackendSink] = {}
    for name, sink_type in sorted(config.routing.target_sink_types.items()):
        children[name] = ReducedBackendSink(
            name=name,
            sink_type=sink_type,
            evidence_dir=evidence_root / name,
            fail=name == fail_sink,
            hang=name == hang_sink,
            delay_seconds=fail_delay_seconds if name == fail_sink else 0.0,
        )
    return children


def _record_for_message(
    *,
    sink: str,
    sink_type: str,
    message: NatsEnvelope,
) -> ReducedSinkRecord:
    labels = tuple(sorted(label for label in message.labels if label))
    return ReducedSinkRecord(
        sink=sink,
        sink_type=sink_type,
        message_id=_message_id(message),
        subject_family=_subject_family(message.subject),
        priority=message.priority,
        classification=message.classification,
        labels=labels,
        route_header=message.headers.get("Nats-Sinks-Route"),
        stream_sequence=message.stream_sequence,
    )


def _expected_routing(
    *,
    routing: RoutingMatchPolicyConfig,
    envelopes: Sequence[NatsEnvelope],
) -> tuple[dict[str, list[str]], list[str]]:
    expected: dict[str, list[str]] = defaultdict(list)
    no_route: list[str] = []
    for envelope in envelopes:
        selection = select_route_targets(envelope, routing)
        if selection.action == "ignore":
            no_route.append(_message_id(envelope))
            continue
        if selection.action == "reject":
            raise MultiSinkRoutingCertificationError(
                f"unexpected rejected envelope in success matrix: {_message_id(envelope)}"
            )
        for target in selection.targets:
            expected[target].append(_message_id(envelope))
    return {name: sorted(values) for name, values in sorted(expected.items())}, no_route


def _actual_by_sink(children: Mapping[str, ReducedBackendSink]) -> dict[str, list[str]]:
    return {
        name: sorted(child.message_ids)
        for name, child in sorted(children.items())
        if child.message_ids
    }


def _assert_expected_messages(
    children: Mapping[str, ReducedBackendSink],
    expected_by_sink: Mapping[str, Sequence[str]],
) -> None:
    actual = _actual_by_sink(children)
    expected = {
        name: sorted(message_ids)
        for name, message_ids in sorted(expected_by_sink.items())
        if message_ids
    }
    if actual != expected:
        raise MultiSinkRoutingCertificationError(
            f"multi-sink routing mismatch: expected {expected}, actual {actual}"
        )


async def _exercise_optional_timeout(config: AppConfig, work_dir: Path) -> bool:
    if config.routing is None:
        raise MultiSinkRoutingCertificationError("multi-sink config has no routing")
    children = _build_reduced_children(
        config,
        work_dir / "optional-timeout",
        hang_sink="file_audit",
    )
    fanout = FanoutSink(children=children, routing=config.routing)
    await fanout.start()
    try:
        await fanout.write_batch([multi_sink_routing_envelopes()[0]])
    finally:
        await fanout.stop()
    required_sinks = {"mysql_audit", "oracle_primary"}
    return all(children[name].message_ids == ["MSG-SECRET-1"] for name in required_sinks)


async def _exercise_required_failure(config: AppConfig, work_dir: Path) -> bool:
    if config.routing is None:
        raise MultiSinkRoutingCertificationError("multi-sink config has no routing")
    children = _build_reduced_children(
        config,
        work_dir / "required-failure",
        fail_sink="mysql_audit",
        fail_delay_seconds=0.02,
    )
    fanout = FanoutSink(children=children, routing=config.routing)
    await fanout.start()
    try:
        try:
            await fanout.write_batch([multi_sink_routing_envelopes()[0]])
        except TemporarySinkError:
            return children["oracle_primary"].message_ids == ["MSG-SECRET-1"]
        return False
    finally:
        await fanout.stop()


async def _exercise_reject_no_route(config: AppConfig, work_dir: Path) -> bool:
    if config.routing is None:
        raise MultiSinkRoutingCertificationError("multi-sink config has no routing")
    routing = config.routing.model_copy(update={"no_match": "reject"})
    children = _build_reduced_children(config, work_dir / "reject-no-route")
    fanout = FanoutSink(children=children, routing=routing)
    await fanout.start()
    try:
        try:
            await fanout.write_batch([multi_sink_routing_envelopes()[3]])
        except PermanentSinkError:
            return True
        return False
    finally:
        await fanout.stop()


def _route_matrix(config: AppConfig) -> list[dict[str, Any]]:
    if config.routing is None:
        raise MultiSinkRoutingCertificationError("multi-sink config has no routing")
    rows: list[dict[str, Any]] = []
    for route in config.routing.routes:
        match = route.match
        rows.append(
            {
                "name": route.name,
                "targets": [target.sink for target in route.targets],
                "match_fields": [
                    field
                    for field, value in (
                        ("subject", match.subject),
                        ("priority", match.priority),
                        ("classification", match.classification),
                        ("labels_all", match.labels_all),
                        ("labels_any", match.labels_any),
                        ("labels_none", match.labels_none),
                        ("headers", match.headers),
                    )
                    if value
                ],
                "static_config_gate": (
                    f"{MULTI_SINK_ROUTING_FLOW_HEADER}={MULTI_SINK_ROUTING_FLOW_VALUE}"
                ),
            }
        )
    return rows


def _envelope(
    *,
    message_id: str,
    subject: str,
    priority: str,
    classification: str,
    labels: Sequence[str],
    route_header: str,
    stream_sequence: int,
) -> NatsEnvelope:
    headers = {
        "Nats-Msg-Id": message_id,
        "Nats-Sinks-Route": route_header,
        MULTI_SINK_ROUTING_FLOW_HEADER: MULTI_SINK_ROUTING_FLOW_VALUE,
    }
    return NatsEnvelope(
        subject=subject,
        data=b'{"event":"synthetic"}',
        headers=headers,
        stream="TEST",
        consumer="MULTI_SINK_ROUTING",
        stream_sequence=stream_sequence,
        consumer_sequence=stream_sequence,
        timestamp=None,
        message_id=message_id,
        redelivered=False,
        pending=0,
        priority=priority,
        classification=classification,
        labels=tuple(labels),
    )


def _message_id(message: NatsEnvelope) -> str:
    if message.message_id:
        return message.message_id
    if message.stream_sequence is not None:
        return f"{message.stream or 'stream'}:{message.stream_sequence}"
    raise MultiSinkRoutingCertificationError("test envelope has no stable message ID")


def _subject_family(subject: str) -> str:
    tokens = [token for token in subject.split(".") if token]
    if len(tokens) < _SUBJECT_FAMILY_MIN_TOKENS:
        return subject
    return ".".join(tokens[:2]) + ".*"
