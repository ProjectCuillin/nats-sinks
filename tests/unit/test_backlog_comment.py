# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for sanitized backlog issue comments."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "comment-backlog-issue.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("comment_backlog_issue", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["comment_backlog_issue"] = module
    spec.loader.exec_module(module)
    return module


def test_comment_script_dry_run_accepts_sanitized_comment(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text(
        """## Planned Work

Implementation will add tests, docs, and release notes.

## Test Plan

Run focused unit tests and the full local check.

## Documentation And Release Notes

Update public documentation and CHANGELOG.md.
""",
        encoding="utf-8",
    )

    result = script.main(
        [
            "--backlog-id",
            "sample-backlog-item",
            "--release",
            "v0.4.0",
            "--status",
            "started",
            "--comment-file",
            str(comment),
            "--dry-run",
        ]
    )

    assert result == 0
    assert "would comment on backlog sample-backlog-item" in capsys.readouterr().out


def test_comment_script_completed_dry_run_mentions_completed_label(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text(
        """## Completed Work

Implemented the feature and updated tests.

## Acceptance Criteria

- [x] Feature behavior is complete.

## Test Plan Evidence

Focused and full checks passed.

## Close-Out Evidence

Ready for release-gated closure after publication.
""",
        encoding="utf-8",
    )

    result = script.main(
        [
            "--backlog-id",
            "sample-backlog-item",
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


def test_completed_backlog_label_is_applied_only_for_completed_status() -> None:
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


def test_comment_script_rejects_public_comment_with_url(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text("Do not publish this locator: https://example.invalid", encoding="utf-8")
    result = script.main(
        [
            "--backlog-id",
            "sample-backlog-item",
            "--release",
            "v0.4.0",
            "--comment-file",
            str(comment),
            "--dry-run",
        ]
    )

    assert result == 1
    assert "must not contain URLs" in capsys.readouterr().err


def test_comment_script_requires_lifecycle_sections(tmp_path: Path, capsys) -> None:
    script = _load_script()
    comment = tmp_path / "comment.md"
    comment.write_text("Implementation will add tests.", encoding="utf-8")

    result = script.main(
        [
            "--backlog-id",
            "sample-backlog-item",
            "--release",
            "v0.4.0",
            "--status",
            "started",
            "--comment-file",
            str(comment),
            "--dry-run",
        ]
    )

    assert result == 1
    assert "missing section" in capsys.readouterr().err


def test_acceptance_checklist_completion_only_changes_acceptance_section() -> None:
    script = _load_script()
    body = """## Problem Statement

- [ ] This is not acceptance.

## Acceptance Criteria

- [ ] First criterion.
- [x] Second criterion.

## Test Plan

- [ ] This is not acceptance either.
"""

    updated = script.acceptance_checklist_complete(body)

    assert "## Problem Statement\n\n- [ ] This is not acceptance." in updated
    assert "- [x] First criterion." in updated
    assert "- [x] Second criterion." in updated
    assert "## Test Plan\n\n- [ ] This is not acceptance either." in updated
