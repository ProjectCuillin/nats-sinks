# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for strict JSON boundary handling found during bug hunting.

Each test in this module is intentionally small and maps to one managed GitHub
bug report.  The defects share one theme: public configuration, automation
manifests, security envelopes, and release evidence must use standards-compliant
JSON and must reject ambiguous structures before downstream code can make
unsafe assumptions.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import math
import secrets
import sys
from pathlib import Path
from types import ModuleType

import pytest

from nats_sinks.core.config import EncryptionConfig, load_json
from nats_sinks.core.encryption import ENCRYPTED_PAYLOAD_KEY, PayloadEncryptor
from nats_sinks.core.errors import ConfigurationError, SerializationError
from nats_sinks.core.errors import ValidationError as FrameworkValidationError
from nats_sinks.core.mission_metadata import parse_mission_metadata_header
from nats_sinks.core.payload import normalize_payload_for_json_storage
from nats_sinks.observability.nats_monitoring import NatsMonitoringError, _json_loads_endpoint
from nats_sinks.observability.policy import write_observability_policy
from nats_sinks.testing.load_profile import LoadPhaseTiming
from nats_sinks.testing.oracle_benchmark import BenchmarkPhaseTiming

ROOT = Path(__file__).resolve().parents[2]
BACKLOG_SYNC_SCRIPT = ROOT / "scripts" / "sync-backlog-issues.py"
BUG_SYNC_SCRIPT = ROOT / "scripts" / "sync-bug-reports.py"


def _load_script(path: Path, module_name: str) -> ModuleType:
    """Import an automation script as a normal Python module for unit tests."""

    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _valid_backlog_item() -> dict[str, object]:
    """Return a minimal public-safe backlog item document."""

    return {
        "id": "strict-json-backlog-item",
        "title": "[Feature]: Strict JSON backlog sample",
        "area": "Testing",
        "priority": "P3 - backlog candidate",
        "target_release": "unscheduled",
        "labels": ["testing"],
        "problem": "The project needs a sample backlog item for strict JSON tests.",
        "proposal": "Validate local JSON before generating public issue text.",
        "users": "Maintainers who manage public backlog items from local files.",
        "delivery_semantics": "No delivery semantics change is expected.",
        "security": "The sample contains no secrets, locators, or payload bodies.",
        "acceptance": ["The strict JSON regression test passes."],
        "tests": "pytest tests/unit/test_bug_hunt_strict_json_boundaries.py",
        "documentation": "Document strict JSON handling in release notes.",
        "closeout": "Close after the release containing the fix is published.",
    }


def _valid_bug_report() -> dict[str, object]:
    """Return a minimal public-safe bug report document."""

    return {
        "id": "strict-json-bug-report",
        "title": "[Bug]: Strict JSON bug sample",
        "area": "Testing",
        "severity": "medium",
        "priority": "P2 - next minor release candidate",
        "target_release": "unscheduled",
        "labels": ["testing"],
        "summary": "A sample managed bug report is needed for strict JSON tests.",
        "observed": "The sample parser accepts ambiguous JSON.",
        "expected": "The sample parser rejects ambiguous JSON.",
        "reproduction": "Run the strict JSON regression test.",
        "failing_test": "tests/unit/test_bug_hunt_strict_json_boundaries.py",
        "impact": "Maintainers need reliable public bug evidence.",
        "delivery_semantics": "No delivery semantics change is expected.",
        "security": "The sample contains no secrets, locators, or payload bodies.",
        "acceptance": ["The strict JSON regression test passes."],
        "tests": "pytest tests/unit/test_bug_hunt_strict_json_boundaries.py",
        "documentation": "Document strict JSON handling in release notes.",
        "closeout": "Close after the release containing the fix is published.",
    }


def _key_b64() -> str:
    """Return generated AES-256 key material for isolated encryption tests."""

    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def test_config_loader_rejects_nonstandard_json_constants(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"delivery":{"retry_backoff_multiplier":NaN}}', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="non-standard JSON constant"):
        load_json(path)


def test_backlog_sync_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    script = _load_script(BACKLOG_SYNC_SCRIPT, "strict_json_backlog_duplicate")
    item = _valid_backlog_item()
    rendered = json.dumps(item).replace(
        '"title": "[Feature]: Strict JSON backlog sample"',
        '"title": "[Feature]: Strict JSON backlog sample", "title": "[Feature]: Duplicate"',
    )
    path = tmp_path / "item.json"
    path.write_text(rendered, encoding="utf-8")

    with pytest.raises(script.BacklogValidationError, match="duplicate JSON object key"):
        script.load_backlog_item(path)


def test_backlog_sync_rejects_nonstandard_json_constants(tmp_path: Path) -> None:
    script = _load_script(BACKLOG_SYNC_SCRIPT, "strict_json_backlog_constant")
    item = _valid_backlog_item()
    rendered = json.dumps(item)[:-1] + ', "ignored_operator_note": NaN}'
    path = tmp_path / "item.json"
    path.write_text(rendered, encoding="utf-8")

    with pytest.raises(script.BacklogValidationError, match="non-standard JSON constant"):
        script.load_backlog_item(path)


def test_bug_report_sync_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    script = _load_script(BUG_SYNC_SCRIPT, "strict_json_bug_duplicate")
    bug = _valid_bug_report()
    rendered = json.dumps(bug).replace(
        '"title": "[Bug]: Strict JSON bug sample"',
        '"title": "[Bug]: Strict JSON bug sample", "title": "[Bug]: Duplicate"',
    )
    path = tmp_path / "bug.json"
    path.write_text(rendered, encoding="utf-8")

    with pytest.raises(script.BugReportValidationError, match="duplicate JSON object key"):
        script.load_bug_report(path)


def test_bug_report_sync_rejects_nonstandard_json_constants(tmp_path: Path) -> None:
    script = _load_script(BUG_SYNC_SCRIPT, "strict_json_bug_constant")
    bug = _valid_bug_report()
    rendered = json.dumps(bug)[:-1] + ', "ignored_operator_note": NaN}'
    path = tmp_path / "bug.json"
    path.write_text(rendered, encoding="utf-8")

    with pytest.raises(script.BugReportValidationError, match="non-standard JSON constant"):
        script.load_bug_report(path)


def test_mission_metadata_rejects_nonstandard_json_constants_at_parse_boundary() -> None:
    with pytest.raises(FrameworkValidationError, match="not valid JSON"):
        parse_mission_metadata_header(
            '{"profile":"mission-event-v1","confidence":NaN}',
            max_bytes=1024,
            allowed_profiles=(),
        )


def test_encryption_payload_rejects_duplicate_json_keys() -> None:
    config = EncryptionConfig(
        enabled=True,
        algorithm="aes-256-gcm",
        key_id="strict-json-key",
        key_b64=_key_b64(),
    )
    encryptor = PayloadEncryptor(config)
    encrypted = json.loads(encryptor.encrypt_bytes(b"strict-json-secret").decode("utf-8"))
    envelope = json.dumps(encrypted[ENCRYPTED_PAYLOAD_KEY], sort_keys=True)
    duplicate_payload = (
        f'{{"_nats_sinks_encryption":{{"schema":"duplicate"}},"_nats_sinks_encryption":{envelope}}}'
    )

    with pytest.raises(SerializationError, match="valid JSON envelope"):
        encryptor.decrypt_payload(duplicate_payload)


def test_encryption_payload_rejects_nonstandard_json_constants() -> None:
    config = EncryptionConfig(
        enabled=True,
        algorithm="aes-256-gcm",
        key_id="strict-json-key",
        key_b64=_key_b64(),
    )
    encryptor = PayloadEncryptor(config)
    encrypted = json.loads(encryptor.encrypt_bytes(b"strict-json-secret").decode("utf-8"))
    encrypted["ignored_operator_note"] = math.nan
    rendered = json.dumps(encrypted)

    with pytest.raises(SerializationError, match="valid JSON envelope"):
        encryptor.decrypt_payload(rendered)


def test_payload_normalization_treats_duplicate_keys_as_ambiguous_json() -> None:
    payload = b'{"kind":"first","kind":"second"}'

    normalized = normalize_payload_for_json_storage(
        payload,
        subject="strict.json.boundary",
        mode="json_or_envelope",
    )

    assert normalized.wrapped is True
    assert normalized.original_format == "text"
    assert normalized.value["payload"] == payload.decode("utf-8")
    with pytest.raises(SerializationError, match="not valid JSON"):
        normalize_payload_for_json_storage(
            payload,
            subject="strict.json.boundary",
            mode="json_only",
        )


def test_nats_monitoring_rejects_duplicate_json_keys() -> None:
    with pytest.raises(NatsMonitoringError, match="valid JSON"):
        _json_loads_endpoint(b'{"server_id":"first","server_id":"second"}', endpoint="/varz")


def test_observability_policy_writer_rejects_nonfinite_raw_dict_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="non-finite"):
        write_observability_policy(
            {"schema_id": "nats_sinks.observability.policy.v1", "bad_value": math.nan},
            tmp_path / "observability.json",
        )

    assert not (tmp_path / "observability.json").exists()


def test_oracle_benchmark_rejects_nonfinite_phase_timings() -> None:
    with pytest.raises(ValueError, match="finite"):
        BenchmarkPhaseTiming(
            phase="write",
            count=1,
            total_seconds=math.nan,
            average_seconds=0.0,
            max_seconds=0.0,
        )


def test_load_profile_rejects_nonfinite_phase_timings() -> None:
    with pytest.raises(ValueError, match="finite"):
        LoadPhaseTiming(
            phase="backend_write",
            count=1,
            total_seconds=math.nan,
            average_seconds=0.0,
            max_seconds=0.0,
        )
