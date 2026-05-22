# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for managed bug-report sync helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync-bug-reports.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_bug_reports", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_bug_reports"] = module
    spec.loader.exec_module(module)
    return module


def _valid_bug() -> dict[str, object]:
    return {
        "id": "sample-bug-report",
        "title": "[Bug]: Sample managed bug",
        "area": "Testing",
        "severity": "medium",
        "priority": "P2 - next minor release candidate",
        "target_release": "unscheduled",
        "labels": ["testing"],
        "summary": "A focused defect needs a managed public bug report.",
        "observed": "The sample behavior fails in a deterministic test.",
        "expected": "The sample behavior should pass after the fix.",
        "reproduction": "Run the committed failing regression test.",
        "failing_test": "Add tests/unit/test_sample_bug.py before changing production code.",
        "impact": "Maintainers need release evidence for this defect.",
        "delivery_semantics": "The defect does not change ACK ordering.",
        "security": "No secrets, payloads, locators, or private details are included.",
        "acceptance": [
            "The failing test is committed before the fix.",
            "The same test passes after the fix.",
        ],
        "tests": "pytest tests/unit/test_sample_bug.py",
        "documentation": "Update CHANGELOG.md and affected docs if behavior changes.",
        "closeout": "Close after the release publishes the fix and evidence.",
    }


def test_load_bug_report_and_render_issue_body(tmp_path: Path) -> None:
    script = _load_script()
    bug_path = tmp_path / "bug.json"
    bug_path.write_text(json.dumps(_valid_bug()), encoding="utf-8")

    bug = script.load_bug_report(bug_path)
    body = script.render_issue_body(bug)

    assert bug.identifier == "sample-bug-report"
    assert "bug" in bug.labels
    assert "testing" in bug.labels
    assert "release-unscheduled" in bug.labels
    assert "priority-p2" not in bug.labels
    assert "severity-medium" in bug.labels
    assert "nats-sinks-bug-id: sample-bug-report" in body
    assert "Priority: `P2 - next minor release candidate`" in body
    assert "## Failing Regression Test" in body
    assert "## Issue Relationships" in body
    assert "- [ ] The failing test is committed before the fix." in body


def test_load_bug_report_accepts_declared_issue_relationships(tmp_path: Path) -> None:
    script = _load_script()
    bug = _valid_bug()
    bug["relationships"] = {
        "blocked_by": ["backlog:test-fixture"],
        "blocks": ["#81"],
        "related": ["bug:adjacent-defect"],
    }
    bug_path = tmp_path / "bug.json"
    bug_path.write_text(json.dumps(bug), encoding="utf-8")

    loaded = script.load_bug_report(bug_path)
    body = script.render_issue_body(loaded)

    assert loaded.relationships["blocked_by"] == ("backlog:test-fixture",)
    assert "- Blocked by: `backlog:test-fixture`" in body
    assert "- Blocks: `#81`" in body
    assert "- Related: `bug:adjacent-defect`" in body


def test_load_bug_report_rejects_secret_like_comment(tmp_path: Path) -> None:
    script = _load_script()
    bug = _valid_bug()
    bug["security"] = "Bad report with password=example"
    bug_path = tmp_path / "bug.json"
    bug_path.write_text(json.dumps(bug), encoding="utf-8")

    try:
        script.load_bug_report(bug_path)
    except script.BugReportValidationError as exc:
        assert "credential assignments" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("secret-like bug report was accepted")


def test_load_bug_report_rejects_invalid_severity(tmp_path: Path) -> None:
    script = _load_script()
    bug = _valid_bug()
    bug["severity"] = "unknown"
    bug_path = tmp_path / "bug.json"
    bug_path.write_text(json.dumps(bug), encoding="utf-8")

    try:
        script.load_bug_report(bug_path)
    except script.BugReportValidationError as exc:
        assert "'severity' must be one of" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("invalid bug severity was accepted")


def test_discover_reports_rejects_duplicate_identifiers(tmp_path: Path) -> None:
    script = _load_script()
    bug = _valid_bug()
    (tmp_path / "one.json").write_text(json.dumps(bug), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps(bug), encoding="utf-8")

    try:
        script.discover_reports(tmp_path)
    except script.BugReportValidationError as exc:
        assert "Duplicate bug identifiers" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("duplicate bug identifiers were accepted")


def test_bug_sync_check_accepts_empty_directory(tmp_path: Path, capsys) -> None:
    script = _load_script()

    result = script.main(["--directory", str(tmp_path), "--check"])

    assert result == 0
    assert "Validated 0 bug report item(s)." in capsys.readouterr().out
