# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for sanitized managed-bug lifecycle comments."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "comment-bug-issue.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("comment_bug_issue", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["comment_bug_issue"] = module
    spec.loader.exec_module(module)
    return module


def test_bug_comment_dry_run_accepts_failing_test_note(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text(
        """## Failing Test

Added a focused regression test under tests/unit.

## Reproduction Evidence

The test fails before the fix.

## Expected Failure

The assertion captures the defect without external services.
""",
        encoding="utf-8",
    )

    result = script.main(
        [
            "--bug-id",
            "sample-bug-report",
            "--release",
            "v0.4.0",
            "--status",
            "failing-test",
            "--comment-file",
            str(comment),
            "--dry-run",
        ]
    )

    assert result == 0
    assert "would comment on bug sample-bug-report" in capsys.readouterr().out


def test_bug_comment_can_attach_sanitized_failing_test(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text(
        """## Failing Test

Added a focused regression test under tests/unit.

## Reproduction Evidence

The test fails before the fix.

## Expected Failure

The assertion captures the defect without external services.
""",
        encoding="utf-8",
    )
    test_file = ROOT / "tests" / "unit" / "test_release_bug_close.py"

    result = script.main(
        [
            "--bug-id",
            "sample-bug-report",
            "--release",
            "v0.4.0",
            "--status",
            "failing-test",
            "--comment-file",
            str(comment),
            "--test-file",
            str(test_file),
            "--dry-run",
        ]
    )

    assert result == 0
    assert "would comment on bug sample-bug-report" in capsys.readouterr().out


def test_bug_comment_rejects_test_file_for_non_failing_status(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text(
        """## Planned Fix

Implement the small fix.

## Test Driven Plan

Run the regression test.

## Documentation And Release Notes

Update public docs if behavior changes.
""",
        encoding="utf-8",
    )

    result = script.main(
        [
            "--bug-id",
            "sample-bug-report",
            "--release",
            "v0.4.0",
            "--status",
            "started",
            "--comment-file",
            str(comment),
            "--test-file",
            str(ROOT / "tests" / "unit" / "test_bug_comment.py"),
            "--dry-run",
        ]
    )

    assert result == 1
    assert "--test-file can only be used" in capsys.readouterr().err


def test_bug_comment_rejects_missing_tdd_sections(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text("The test failed.", encoding="utf-8")

    result = script.main(
        [
            "--bug-id",
            "sample-bug-report",
            "--release",
            "v0.4.0",
            "--status",
            "failing-test",
            "--comment-file",
            str(comment),
            "--dry-run",
        ]
    )

    assert result == 1
    assert "missing section" in capsys.readouterr().err


def test_bug_comment_rejects_public_comment_with_url(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text("Bad note with https://example.invalid", encoding="utf-8")

    result = script.main(
        [
            "--bug-id",
            "sample-bug-report",
            "--release",
            "v0.4.0",
            "--comment-file",
            str(comment),
            "--dry-run",
        ]
    )

    assert result == 1
    assert "must not contain URLs" in capsys.readouterr().err


def test_bug_comment_completed_dry_run_mentions_completed_label(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text(
        """## Completed Fix

Implemented the fix and regression coverage.

## Acceptance Criteria

- [x] Regression test passes.

## Regression Test Evidence

Focused regression passed.

## Verification Evidence

Full local validation passed.

## Close-Out Evidence

Ready for release-gated closure after publication.
""",
        encoding="utf-8",
    )

    result = script.main(
        [
            "--bug-id",
            "sample-bug-report",
            "--release",
            "v0.4.0",
            "--status",
            "completed",
            "--comment-file",
            str(comment),
            "--dry-run",
        ]
    )

    assert result == 0
    assert "would add label 'completed'" in capsys.readouterr().out


def test_completed_bug_label_is_applied_only_for_completed_status() -> None:
    script = _load_script()
    calls: list[tuple[str, object]] = []

    class FakeSync:
        def ensure_labels(self, repo: str, labels: list[str], *, dry_run: bool) -> None:
            calls.append(("ensure_labels", (repo, labels, dry_run)))

        def _run_gh(self, args: list[str]) -> None:
            calls.append(("run_gh", args))

    started_args = argparse.Namespace(status="started", repo="ProjectCuillin/nats-sinks")
    script._apply_completed_label(FakeSync(), started_args, 14)
    assert calls == []

    completed_args = argparse.Namespace(status="completed", repo="ProjectCuillin/nats-sinks")
    script._apply_completed_label(FakeSync(), completed_args, 14)

    assert calls == [
        ("ensure_labels", ("ProjectCuillin/nats-sinks", ["completed"], False)),
        (
            "run_gh",
            [
                "issue",
                "edit",
                "14",
                "--repo",
                "ProjectCuillin/nats-sinks",
                "--add-label",
                "completed",
            ],
        ),
    ]


def test_bug_acceptance_checklist_completion_only_changes_acceptance_section() -> None:
    script = _load_script()
    body = """## Summary

- [ ] This is not acceptance.

## Acceptance Criteria

- [ ] Regression test is committed.
- [x] Fix is verified.

## Test Plan

- [ ] This is not acceptance either.
"""

    updated = script.acceptance_checklist_complete(body)

    assert "## Summary\n\n- [ ] This is not acceptance." in updated
    assert "- [x] Regression test is committed." in updated
    assert "- [x] Fix is verified." in updated
    assert "## Test Plan\n\n- [ ] This is not acceptance either." in updated
