# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for copying managed issue labels to pull requests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync-pr-labels.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_pr_labels", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_pr_labels"] = module
    spec.loader.exec_module(module)
    return module


def test_detect_source_issues_uses_explicit_branch_and_body_references() -> None:
    script = _load_script()
    context = script.PullRequestContext(
        number=77,
        head_ref_name="issue-123-example",
        body="Related #456\nAlso references #123 again.",
    )

    assert script.detect_source_issues(context, [99]) == (99, 123, 456)


def test_detect_source_issues_ignores_markdown_code_placeholders() -> None:
    script = _load_script()
    context = script.PullRequestContext(
        number=77,
        head_ref_name="maintenance-branch",
        body=("Use `Related #123` as an example.\n```text\nRelated #234\n```\nRelated #456"),
    )

    assert script.detect_source_issues(context, []) == (456,)


def test_sync_pr_labels_copies_deduplicated_labels_from_all_source_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    calls: list[list[str]] = []

    def fake_run_gh(args: list[str], **kwargs: object) -> dict[str, Any] | None:
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return {
                "number": 77,
                "headRefName": "bug-123-example",
                "body": "Related #456",
            }
        if args[:3] == ["issue", "view", "123"]:
            return {
                "labels": [
                    {"name": "bug"},
                    {"name": "release-v0.4.1"},
                ]
            }
        if args[:3] == ["issue", "view", "456"]:
            return {
                "labels": [
                    {"name": "bug"},
                    {"name": "security"},
                ]
            }
        if args[:3] == ["issue", "edit", "77"]:
            return None
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    copied = script.sync_pr_labels(repo="ProjectCuillin/nats-sinks", pr_number=77)

    assert copied == ("bug", "release-v0.4.1", "security")
    assert calls[-1] == [
        "issue",
        "edit",
        "77",
        "--repo",
        "ProjectCuillin/nats-sinks",
        "--add-label",
        "bug",
        "--add-label",
        "release-v0.4.1",
        "--add-label",
        "security",
    ]


def test_sync_pr_labels_noops_without_source_issue(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    script = _load_script()

    def fake_run_gh(args: list[str], **kwargs: object) -> dict[str, Any]:
        if args[:2] == ["pr", "view"]:
            return {"number": 77, "headRefName": "maintenance", "body": ""}
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    assert script.sync_pr_labels(repo="ProjectCuillin/nats-sinks", pr_number=77) == ()
    assert "No source issue references found" in capsys.readouterr().out


def test_issue_label_payload_rejects_control_characters() -> None:
    script = _load_script()

    with pytest.raises(script.PullRequestLabelSyncError, match="control characters"):
        script._label_names([{"name": "bad\nlabel"}])
