# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Approve a GitHub pull request only when it targets a non-main branch.

The project uses pull requests for two different control boundaries:

* issue and bug branches merge into release or feature branches;
* release branches merge into ``main`` when a public release is explicitly
  approved.

Those boundaries must not be treated the same. This helper intentionally
supports automated approval only for the first case. It refuses every pull
request whose base branch is ``main`` so release approvals remain a deliberate
maintainer action.

The helper is small and dependency-free on purpose. It shells out to the GitHub
CLI with argument lists, bounded timeouts, and JSON parsing through the standard
library. Unit tests monkeypatch the command runner, so tests never make network
calls.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_REPO = "ProjectCuillin/nats-sinks"
MAIN_BRANCH = "main"
DEFAULT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class PullRequestSummary:
    """Validated subset of GitHub pull request metadata used for approval.

    The helper intentionally keeps only the fields needed for the safety
    decision. Keeping this structure small makes it easier to audit that the
    approval gate cannot accidentally depend on untrusted or irrelevant data.
    """

    number: int
    base_ref_name: str
    head_ref_name: str
    state: str
    is_draft: bool
    author_login: str | None
    url: str | None


class ApprovalError(ValueError):
    """Raised when a pull request is not eligible for automated approval."""


def _run_gh(
    args: Sequence[str],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    capture_json: bool = False,
) -> Any:
    """Run ``gh`` with safe subprocess defaults and optional JSON parsing."""

    gh_path = shutil.which("gh")
    if gh_path is None:
        raise ApprovalError("GitHub CLI is required.")
    completed = subprocess.run(  # noqa: S603 - fixed executable; args are an explicit list.
        [gh_path, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = (
            completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        )
        command = " ".join(["gh", *args[:2]])
        raise ApprovalError(f"GitHub CLI command failed while running {command!r}: {detail}")
    if not capture_json:
        return completed.stdout
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ApprovalError("GitHub CLI returned invalid JSON.") from exc


def load_pull_request(*, repo: str, number: int) -> PullRequestSummary:
    """Fetch and normalize the pull request metadata required by the gate."""

    raw = _run_gh(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,baseRefName,headRefName,state,isDraft,author,url",
        ],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise ApprovalError("GitHub CLI returned an unexpected PR payload.")

    author = raw.get("author")
    author_login = author.get("login") if isinstance(author, dict) else None
    return PullRequestSummary(
        number=int(raw["number"]),
        base_ref_name=str(raw.get("baseRefName") or ""),
        head_ref_name=str(raw.get("headRefName") or ""),
        state=str(raw.get("state") or ""),
        is_draft=bool(raw.get("isDraft")),
        author_login=str(author_login) if author_login else None,
        url=str(raw.get("url")) if raw.get("url") else None,
    )


def validate_pull_request_for_auto_approval(
    pr: PullRequestSummary,
    *,
    expected_author: str | None = None,
    allow_draft: bool = False,
) -> None:
    """Raise ``ApprovalError`` unless the PR is safe to auto-approve."""

    if pr.base_ref_name == MAIN_BRANCH:
        raise ApprovalError("Refusing to auto-approve a pull request targeting main.")
    if pr.state != "OPEN":
        raise ApprovalError(f"Refusing to auto-approve PR #{pr.number} because it is {pr.state}.")
    if not pr.base_ref_name or not pr.head_ref_name:
        raise ApprovalError("Refusing to auto-approve a pull request with missing branch metadata.")
    if pr.base_ref_name == pr.head_ref_name:
        raise ApprovalError("Refusing to auto-approve a pull request from a branch into itself.")
    if pr.is_draft and not allow_draft:
        raise ApprovalError("Refusing to auto-approve a draft pull request.")
    if expected_author and pr.author_login != expected_author:
        raise ApprovalError(
            f"Refusing to auto-approve PR #{pr.number}: expected author "
            f"{expected_author!r}, got {pr.author_login!r}."
        )


def approve_pull_request(*, repo: str, number: int, body: str) -> None:
    """Submit the approving review through the GitHub CLI."""

    _run_gh(["pr", "review", str(number), "--repo", repo, "--approve", "--body", body])


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface for the approval helper."""

    parser = argparse.ArgumentParser(
        description="Approve a GitHub PR only when its base branch is not main."
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository as OWNER/REPO.")
    parser.add_argument("--pr", type=int, required=True, help="Pull request number.")
    parser.add_argument(
        "--expected-author",
        default=os.environ.get("NATS_SINKS_PR_AUTO_APPROVE_EXPECTED_AUTHOR"),
        help="Only approve when the PR author login matches this value.",
    )
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Allow approving draft PRs. The default is to require ready PRs.",
    )
    parser.add_argument(
        "--body",
        default=(
            "Automated non-main issue-branch approval. This approval is only "
            "valid for a PR whose base branch is not main; release PRs into "
            "main remain manual."
        ),
        help="Review body to post with the approval.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate without approving.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the target PR and approve it unless this is a dry run."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        pr = load_pull_request(repo=args.repo, number=args.pr)
        validate_pull_request_for_auto_approval(
            pr,
            expected_author=args.expected_author,
            allow_draft=args.allow_draft,
        )
        if args.dry_run:
            sys.stdout.write(f"PR #{pr.number} is eligible for non-main auto-approval.\n")
            return 0
        approve_pull_request(repo=args.repo, number=pr.number, body=args.body)
    except (ApprovalError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    sys.stdout.write(f"Approved PR #{pr.number} ({pr.head_ref_name} -> {pr.base_ref_name}).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
