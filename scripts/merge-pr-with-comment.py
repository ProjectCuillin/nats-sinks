#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Merge a GitHub pull request only after posting test evidence.

The project workflow treats pull requests as the public review boundary for
release branches, issue branches, and bug branches. A merge without a short
evidence comment makes later release review harder, because maintainers must
reconstruct what was tested from private chat history or local terminal output.

This helper posts a sanitized pull request comment first and then invokes
``gh pr merge``. If the comment cannot be validated or posted, the merge never
starts. The helper intentionally uses fixed GitHub CLI argument lists, refuses
draft and non-open pull requests by default, and reuses the public issue text
validator so merge evidence does not leak secrets, private endpoints, payloads,
or local environment details.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

SCRIPT_DIR = Path(__file__).resolve().parent
SYNC_SCRIPT = SCRIPT_DIR / "sync-backlog-issues.py"
MAX_GITHUB_NUMBER = 999_999_999
MAX_COMMENT_BYTES = 12_000
TEST_EVIDENCE_HEADINGS = frozenset(
    {
        "test evidence",
        "test results",
        "tests",
        "validation evidence",
        "verification evidence",
    }
)
MERGE_METHOD_FLAGS = {
    "merge": "--merge",
    "squash": "--squash",
    "rebase": "--rebase",
}


@dataclass(frozen=True)
class PullRequestSummary:
    """Small public PR shape required before a merge can proceed."""

    number: int
    state: str
    is_draft: bool
    base_ref_name: str
    head_ref_name: str
    merge_state_status: str
    url: str


class PullRequestMergeError(RuntimeError):
    """Raised when the guarded PR merge helper cannot continue safely."""


def _load_sync_script() -> ModuleType:
    """Load the backlog sync validator without importing a hyphenated module."""

    spec = importlib.util.spec_from_file_location("sync_backlog_issues", SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise PullRequestMergeError("Unable to load scripts/sync-backlog-issues.py.")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_backlog_issues"] = module
    spec.loader.exec_module(module)
    return module


def _decode_json_output(value: str) -> object:
    """Decode GitHub CLI JSON without leaking raw command output."""

    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise PullRequestMergeError("GitHub CLI returned malformed JSON.") from exc


def _run_gh(args: Sequence[str], *, capture_json: bool = False) -> object:
    """Run GitHub CLI with a fixed executable and argument list."""

    gh_executable = shutil.which("gh")
    if gh_executable is None:
        raise PullRequestMergeError("GitHub CLI is required.")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable with argument list.
            [gh_executable, *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.CalledProcessError as exc:
        raise PullRequestMergeError(exc.stderr.strip() or str(exc)) from exc
    if capture_json:
        return _decode_json_output(completed.stdout)
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    return None


def _validate_number(value: int, *, field: str) -> int:
    """Validate GitHub issue and pull request numbers before API use."""

    if value <= 0 or value > MAX_GITHUB_NUMBER:
        raise PullRequestMergeError(f"{field} must be between 1 and 999999999.")
    return value


def _coerce_number(value: object, *, field: str) -> int:
    """Coerce GitHub JSON number fields without accepting booleans."""

    if isinstance(value, bool):
        raise PullRequestMergeError(f"{field} must be a number.")
    if isinstance(value, int):
        return _validate_number(value, field=field)
    if isinstance(value, str) and value.isdecimal():
        return _validate_number(int(value), field=field)
    raise PullRequestMergeError(f"{field} must be a number.")


def _has_markdown_heading(text: str, headings: frozenset[str]) -> bool:
    """Return true when text contains one of the required evidence headings."""

    for line in text.splitlines():
        normalized = line.strip().lstrip("#").strip().casefold()
        if normalized in headings:
            return True
    return False


def read_validated_comment(path: Path, *, require_test_evidence: bool = True) -> str:
    """Read and validate the public pull request merge comment."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PullRequestMergeError(f"Unable to read comment file {path}.") from exc
    normalized = text.strip()
    if not normalized:
        raise PullRequestMergeError("Merge comment file must not be empty.")
    if len(normalized.encode("utf-8")) > MAX_COMMENT_BYTES:
        raise PullRequestMergeError("Merge comment file is too large for workflow evidence.")
    if require_test_evidence and not _has_markdown_heading(
        normalized,
        TEST_EVIDENCE_HEADINGS,
    ):
        raise PullRequestMergeError(
            "Merge comment must include a Test Evidence, Test Results, Tests, "
            "Validation Evidence, or Verification Evidence heading."
        )
    sync = _load_sync_script()
    try:
        sync.validate_public_issue_text(normalized, path=path, field="merge comment")
    except sync.BacklogValidationError as exc:
        raise PullRequestMergeError(str(exc)) from exc
    return normalized


def load_pull_request(repo: str, pr_number: int) -> PullRequestSummary:
    """Fetch and validate the PR state needed before merge."""

    _validate_number(pr_number, field="pull request number")
    raw = _run_gh(
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "number,state,isDraft,baseRefName,headRefName,mergeStateStatus,url",
        ],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise PullRequestMergeError("Unexpected GitHub PR payload.")
    return PullRequestSummary(
        number=_coerce_number(raw.get("number"), field="pull request number"),
        state=str(raw.get("state") or ""),
        is_draft=bool(raw.get("isDraft")),
        base_ref_name=str(raw.get("baseRefName") or ""),
        head_ref_name=str(raw.get("headRefName") or ""),
        merge_state_status=str(raw.get("mergeStateStatus") or ""),
        url=str(raw.get("url") or ""),
    )


def validate_pull_request(pr: PullRequestSummary, *, allow_draft: bool = False) -> None:
    """Reject PR states where an automated local merge would be unsafe."""

    if pr.state != "OPEN":
        raise PullRequestMergeError(f"Pull request #{pr.number} is {pr.state}, not OPEN.")
    if pr.is_draft and not allow_draft:
        raise PullRequestMergeError(f"Pull request #{pr.number} is a draft.")
    if not pr.base_ref_name or not pr.head_ref_name:
        raise PullRequestMergeError("Pull request branch metadata is incomplete.")


def post_merge_comment(
    *,
    repo: str,
    pr_number: int,
    comment: str,
    dry_run: bool = False,
) -> None:
    """Post the validated evidence comment to the pull request."""

    validated_pr = str(_validate_number(pr_number, field="pull request number"))
    if dry_run:
        sys.stdout.write(f"would comment on PR #{validated_pr} before merge\n")
        return
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(comment)
        handle.write("\n")
        comment_path = Path(handle.name)
    try:
        _run_gh(
            [
                "issue",
                "comment",
                validated_pr,
                "--repo",
                repo,
                "--body-file",
                str(comment_path),
            ]
        )
    finally:
        comment_path.unlink(missing_ok=True)


def merge_pull_request(
    *,
    repo: str,
    pr_number: int,
    method: str,
    delete_branch: bool,
    dry_run: bool = False,
) -> None:
    """Merge the pull request after the evidence comment has been posted."""

    validated_pr = str(_validate_number(pr_number, field="pull request number"))
    method_flag = MERGE_METHOD_FLAGS[method]
    if dry_run:
        branch_note = " and delete branch" if delete_branch else ""
        action = "merge" if method == "merge" else f"{method} merge"
        sys.stdout.write(f"would {action} PR #{validated_pr}{branch_note}\n")
        return
    args = ["pr", "merge", validated_pr, "--repo", repo, method_flag]
    if delete_branch:
        args.append("--delete-branch")
    _run_gh(args)


def guarded_merge(
    *,
    repo: str,
    pr_number: int,
    comment_file: Path,
    method: str = "merge",
    delete_branch: bool = False,
    allow_draft: bool = False,
    dry_run: bool = False,
) -> PullRequestSummary:
    """Validate, comment, and merge a PR in the required order."""

    comment = read_validated_comment(comment_file)
    pr = load_pull_request(repo, pr_number)
    validate_pull_request(pr, allow_draft=allow_draft)
    post_merge_comment(repo=repo, pr_number=pr.number, comment=comment, dry_run=dry_run)
    merge_pull_request(
        repo=repo,
        pr_number=pr.number,
        method=method,
        delete_branch=delete_branch,
        dry_run=dry_run,
    )
    return pr


def build_parser(sync: ModuleType) -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Post a sanitized test-evidence comment before merging a pull request."
    )
    parser.add_argument("--repo", default=sync.DEFAULT_REPO)
    parser.add_argument("--pr", type=int, required=True, help="Pull request number.")
    parser.add_argument(
        "--comment-file",
        type=Path,
        required=True,
        help="Markdown file containing sanitized merge evidence.",
    )
    parser.add_argument(
        "--method",
        choices=tuple(MERGE_METHOD_FLAGS),
        default="merge",
        help="GitHub merge method.",
    )
    parser.add_argument("--delete-branch", action="store_true")
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    sync = _load_sync_script()
    args = build_parser(sync).parse_args(argv)
    try:
        pr = guarded_merge(
            repo=args.repo,
            pr_number=args.pr,
            comment_file=args.comment_file,
            method=args.method,
            delete_branch=args.delete_branch,
            allow_draft=args.allow_draft,
            dry_run=args.dry_run,
        )
    except PullRequestMergeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    verb = "Would merge" if args.dry_run else "Merged"
    sys.stdout.write(
        f"{verb} PR #{pr.number} from {pr.head_ref_name} into {pr.base_ref_name} "
        "after posting merge evidence.\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through tests and CLI use.
    raise SystemExit(main())
