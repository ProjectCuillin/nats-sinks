# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for guarded pull request merge comments."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "merge-pr-with-comment.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("merge_pr_with_comment", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["merge_pr_with_comment"] = module
    spec.loader.exec_module(module)
    return module


def _comment_file(tmp_path: Path, text: str | None = None) -> Path:
    path = tmp_path / "merge-comment.md"
    path.write_text(
        text
        or """## Test Evidence

- `scripts/check.sh` passed in the local release workspace.
""",
        encoding="utf-8",
    )
    return path


def _open_pr_payload() -> dict[str, Any]:
    return {
        "number": 219,
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "release-v0.4.1",
        "headRefName": "issue-220-pr-merge-comment-helper",
        "mergeStateStatus": "CLEAN",
        "url": "https://example.invalid/pull/219",
    }


def test_guarded_merge_posts_comment_before_merge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = _load_script()
    calls: list[list[str]] = []

    def fake_run_gh(args: list[str], **kwargs: object) -> dict[str, Any] | None:
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return _open_pr_payload()
        if args[:3] == ["issue", "comment", "219"]:
            return None
        if args[:3] == ["pr", "merge", "219"]:
            return None
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    pr = script.guarded_merge(
        repo="ProjectCuillin/nats-sinks",
        pr_number=219,
        comment_file=_comment_file(tmp_path),
    )

    assert pr.number == 219
    assert calls[0][:2] == ["pr", "view"]
    assert calls[1][:3] == ["issue", "comment", "219"]
    assert calls[2] == [
        "pr",
        "merge",
        "219",
        "--repo",
        "ProjectCuillin/nats-sinks",
        "--merge",
    ]


def test_guarded_merge_supports_squash_and_delete_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = _load_script()
    calls: list[list[str]] = []

    def fake_run_gh(args: list[str], **kwargs: object) -> dict[str, Any] | None:
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return _open_pr_payload()
        if args[:3] == ["issue", "comment", "219"]:
            return None
        if args[:3] == ["pr", "merge", "219"]:
            return None
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    script.guarded_merge(
        repo="ProjectCuillin/nats-sinks",
        pr_number=219,
        comment_file=_comment_file(tmp_path),
        method="squash",
        delete_branch=True,
    )

    assert calls[-1] == [
        "pr",
        "merge",
        "219",
        "--repo",
        "ProjectCuillin/nats-sinks",
        "--squash",
        "--delete-branch",
    ]


def test_guarded_merge_dry_run_validates_without_mutating_github(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script()
    calls: list[list[str]] = []

    def fake_run_gh(args: list[str], **kwargs: object) -> dict[str, Any]:
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return _open_pr_payload()
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    pr = script.guarded_merge(
        repo="ProjectCuillin/nats-sinks",
        pr_number=219,
        comment_file=_comment_file(tmp_path),
        dry_run=True,
    )

    assert pr.number == 219
    assert calls == [
        [
            "pr",
            "view",
            "219",
            "--repo",
            "ProjectCuillin/nats-sinks",
            "--json",
            "number,state,isDraft,baseRefName,headRefName,mergeStateStatus,url",
        ]
    ]
    output = capsys.readouterr().out
    assert "would comment on PR #219 before merge" in output
    assert "would merge PR #219" in output


def test_merge_comment_requires_test_evidence_heading(tmp_path: Path) -> None:
    script = _load_script()

    with pytest.raises(script.PullRequestMergeError, match="Test Evidence"):
        script.read_validated_comment(
            _comment_file(
                tmp_path,
                """## Summary

- Local validation passed.
""",
            )
        )


def test_merge_comment_rejects_sensitive_text(tmp_path: Path) -> None:
    script = _load_script()

    with pytest.raises(script.PullRequestMergeError, match="credential assignments"):
        script.read_validated_comment(
            _comment_file(
                tmp_path,
                """## Test Evidence

- password=example was used in a local test.
""",
            )
        )


def test_validate_pull_request_refuses_draft() -> None:
    script = _load_script()
    pr = script.PullRequestSummary(
        number=219,
        state="OPEN",
        is_draft=True,
        base_ref_name="release-v0.4.1",
        head_ref_name="issue-220-pr-merge-comment-helper",
        merge_state_status="CLEAN",
        url="",
    )

    with pytest.raises(script.PullRequestMergeError, match="draft"):
        script.validate_pull_request(pr)


def test_validate_pull_request_refuses_non_open_state() -> None:
    script = _load_script()
    pr = script.PullRequestSummary(
        number=219,
        state="MERGED",
        is_draft=False,
        base_ref_name="release-v0.4.1",
        head_ref_name="issue-220-pr-merge-comment-helper",
        merge_state_status="CLEAN",
        url="",
    )

    with pytest.raises(script.PullRequestMergeError, match="not OPEN"):
        script.validate_pull_request(pr)
