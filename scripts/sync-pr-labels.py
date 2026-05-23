#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Copy managed GitHub issue labels onto a pull request.

The repository workflow keeps feature, bug, and release planning metadata on
GitHub Issues. Pull requests should carry the same searchable labels so
maintainers can filter work consistently while it moves through branch review.
This helper reads one or more source issues, collects their labels, and applies
those labels to the pull request with `gh issue edit --add-label`. GitHub pull
requests are issue records for label purposes, and the issue-oriented command
avoids unrelated pull request GraphQL fields that are not needed for label
management.

The helper intentionally copies labels only. GitHub Issue fields, such as the
official Priority field, are not pull request labels and remain managed by the
issue-planning sync scripts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

ISSUE_REFERENCE_RE = re.compile(r"(?<![\w/])#(?P<number>[1-9][0-9]{0,8})(?![\w-])")
BRANCH_ISSUE_RE = re.compile(
    r"^(?:issue|feature|bug|bugfix|hotfix)-(?P<number>[1-9][0-9]{0,8})(?:-|$)"
)
ASCII_CONTROL_MAX = 31
ASCII_DELETE = 127
MAX_GITHUB_NUMBER = 999_999_999


@dataclass(frozen=True)
class PullRequestContext:
    """Small PR shape required for source issue detection."""

    number: int
    head_ref_name: str
    body: str


class PullRequestLabelSyncError(RuntimeError):
    """Raised when pull request label sync cannot continue safely."""


def _run_gh(args: Sequence[str], *, capture_json: bool = False) -> object:
    """Run GitHub CLI with a fixed executable and argument list."""

    gh_executable = shutil.which("gh")
    if gh_executable is None:
        raise PullRequestLabelSyncError("GitHub CLI is required.")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable with argument list.
            [gh_executable, *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        raise PullRequestLabelSyncError(exc.stderr.strip() or str(exc)) from exc
    if capture_json:
        return json.loads(completed.stdout or "{}")
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    return None


def _contains_ascii_control(value: str) -> bool:
    """Return true when a GitHub label is unsafe for terminal output."""

    return any(
        ord(character) <= ASCII_CONTROL_MAX or ord(character) == ASCII_DELETE for character in value
    )


def _validate_number(value: int, *, field: str) -> int:
    """Validate GitHub issue and pull request numbers before API use."""

    if value <= 0 or value > MAX_GITHUB_NUMBER:
        raise PullRequestLabelSyncError(f"{field} must be between 1 and 999999999.")
    return value


def _label_names(raw_labels: object) -> tuple[str, ...]:
    """Extract validated label names from a GitHub JSON payload."""

    if not isinstance(raw_labels, list):
        raise PullRequestLabelSyncError("GitHub label payload must be a list.")
    labels: list[str] = []
    for raw_label in raw_labels:
        if not isinstance(raw_label, dict) or not isinstance(raw_label.get("name"), str):
            raise PullRequestLabelSyncError("GitHub label payload contains an invalid label.")
        label = raw_label["name"].strip()
        if not label:
            continue
        if _contains_ascii_control(label):
            raise PullRequestLabelSyncError(
                "GitHub label names must not contain control characters."
            )
        labels.append(label)
    return tuple(dict.fromkeys(labels))


def load_pull_request(repo: str, pr_number: int) -> PullRequestContext:
    """Load the PR body and source branch for automatic issue detection."""

    _validate_number(pr_number, field="pull request number")
    raw = _run_gh(
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "number,headRefName,body",
        ],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise PullRequestLabelSyncError("Unexpected GitHub PR payload.")
    return PullRequestContext(
        number=int(raw["number"]),
        head_ref_name=str(raw.get("headRefName") or ""),
        body=str(raw.get("body") or ""),
    )


def detect_source_issues(
    context: PullRequestContext,
    explicit_issues: Iterable[int],
) -> tuple[int, ...]:
    """Return explicit, branch-derived, and body-derived source issue numbers."""

    issues: list[int] = []
    for issue in explicit_issues:
        issues.append(_validate_number(issue, field="issue number"))

    branch_match = BRANCH_ISSUE_RE.search(context.head_ref_name)
    if branch_match is not None:
        issues.append(int(branch_match.group("number")))

    for match in ISSUE_REFERENCE_RE.finditer(context.body):
        issues.append(int(match.group("number")))

    return tuple(dict.fromkeys(issues))


def load_issue_labels(repo: str, issue_number: int) -> tuple[str, ...]:
    """Return all labels currently attached to one source issue."""

    _validate_number(issue_number, field="issue number")
    raw = _run_gh(
        [
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repo,
            "--json",
            "labels",
        ],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise PullRequestLabelSyncError("Unexpected GitHub issue payload.")
    return _label_names(raw.get("labels", []))


def apply_pr_labels(
    repo: str,
    pr_number: int,
    labels: Iterable[str],
    *,
    dry_run: bool = False,
) -> tuple[str, ...]:
    """Apply a stable de-duplicated label list to the PR."""

    unique_labels = tuple(dict.fromkeys(label for label in labels if label))
    if not unique_labels:
        return ()
    if dry_run:
        sys.stdout.write(f"would add PR labels: {', '.join(unique_labels)}\n")
        return unique_labels

    args = [
        "issue",
        "edit",
        str(_validate_number(pr_number, field="pull request number")),
        "--repo",
        repo,
    ]
    for label in unique_labels:
        args.extend(["--add-label", label])
    _run_gh(args)
    return unique_labels


def sync_pr_labels(
    *,
    repo: str,
    pr_number: int,
    explicit_issues: Iterable[int] = (),
    dry_run: bool = False,
) -> tuple[str, ...]:
    """Copy labels from detected source issues to a pull request."""

    context = load_pull_request(repo, pr_number)
    issue_numbers = detect_source_issues(context, explicit_issues)
    if not issue_numbers:
        sys.stdout.write("No source issue references found; no PR labels copied.\n")
        return ()

    labels: list[str] = []
    for issue_number in issue_numbers:
        labels.extend(load_issue_labels(repo, issue_number))
    copied = apply_pr_labels(repo, context.number, labels, dry_run=dry_run)
    if copied:
        sys.stdout.write(
            f"Copied {len(copied)} label(s) from issue(s) "
            f"{', '.join(f'#{issue}' for issue in issue_numbers)} to PR #{context.number}.\n"
        )
    return copied


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(description="Copy GitHub issue labels to a pull request.")
    parser.add_argument("--repo", default="ProjectCuillin/nats-sinks")
    parser.add_argument("--pr", type=int, required=True, help="Pull request number.")
    parser.add_argument(
        "--issue",
        type=int,
        action="append",
        default=[],
        help=(
            "Source issue number. May be repeated. If omitted, the branch and PR body are scanned."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    args = build_parser().parse_args(argv)
    try:
        sync_pr_labels(
            repo=args.repo,
            pr_number=args.pr,
            explicit_issues=args.issue,
            dry_run=args.dry_run,
        )
    except PullRequestLabelSyncError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through tests and CLI use.
    raise SystemExit(main())
