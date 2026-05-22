# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Close release-labeled backlog issues after a GitHub Release exists.

Backlog issues are intentionally left open while implementation is merely
complete locally or merged to the repository. The release workflow calls this
helper after the GitHub Release has been created or updated, so issue close-out
matches the public release boundary rather than an earlier development event.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

SCRIPT_DIR = Path(__file__).resolve().parent
SYNC_SCRIPT = SCRIPT_DIR / "sync-backlog-issues.py"
MAX_ISSUE_LIMIT = 500


def _load_sync_script() -> ModuleType:
    """Load the backlog sync helper for validation and GitHub CLI calls."""

    spec = importlib.util.spec_from_file_location("sync_backlog_issues", SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit("Unable to load scripts/sync-backlog-issues.py.")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_backlog_issues"] = module
    spec.loader.exec_module(module)
    return module


def has_backlog_marker(body: str, marker_prefix: str) -> bool:
    """Return true when an issue body carries the managed backlog marker."""

    return f"{marker_prefix} " in body


def acceptance_criteria_complete(body: str) -> bool:
    """Return true when all Acceptance Criteria checklist items are checked."""

    in_acceptance = False
    saw_item = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_acceptance = stripped == "## Acceptance Criteria"
            continue
        if not in_acceptance:
            continue
        lowered = stripped.lower()
        if lowered.startswith("- [ ] "):
            return False
        if lowered.startswith("- [x] "):
            saw_item = True
    return saw_item


def has_closeout_evidence(comments: object) -> bool:
    """Return true when an issue has a sanitized close-out evidence comment."""

    if not isinstance(comments, list):
        return False
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body", ""))
        if "Close-Out Evidence" in body and "Test Plan Evidence" in body:
            return True
    return False


def release_close_ready(issue: dict, marker_prefix: str) -> tuple[bool, str]:
    """Validate that a release-labeled issue is safe to close automatically."""

    body = str(issue.get("body", ""))
    if not has_backlog_marker(body, marker_prefix):
        return False, "missing managed backlog marker"
    if not acceptance_criteria_complete(body):
        return False, "acceptance criteria are not fully checked"
    if not has_closeout_evidence(issue.get("comments", [])):
        return False, "missing close-out evidence comment"
    return True, "ready"


def render_close_comment(release: str) -> str:
    """Render a safe release close-out comment for a backlog issue."""

    return f"""## Release Close-Out

Released in `{release}`.

This issue is being closed by release automation after the GitHub Release was
created. The release contains the package artifacts, checksum manifest, SBOM
evidence, release notes, documentation updates, and validation results prepared
for this release.

If follow-up work remains, open a new backlog item rather than reopening this
released issue.
"""


def list_released_backlog_issues(
    sync: ModuleType, *, repo: str, release: str, limit: int
) -> list[dict]:
    """List open backlog issues labeled for a release."""

    raw = sync._run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--label",
            "backlog",
            "--label",
            f"release-{release}",
            "--json",
            "number,title,url",
            "--limit",
            str(limit),
        ],
        capture_json=True,
    )
    if not isinstance(raw, list):
        raise SystemExit("Unexpected GitHub CLI issue list response.")
    issues = [issue for issue in raw if isinstance(issue, dict)]
    for issue in issues:
        number = int(issue["number"])
        issue["body"] = issue_body(sync, repo=repo, number=number)
        issue["comments"] = issue_comments(sync, repo=repo, number=number)
    return issues


def issue_body(sync: ModuleType, *, repo: str, number: int) -> str:
    """Fetch an issue body because `gh issue list` does not expose it."""

    raw = sync._run_gh(
        ["issue", "view", str(number), "--repo", repo, "--json", "body"],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise SystemExit("Unexpected GitHub CLI issue view response.")
    return str(raw.get("body", ""))


def issue_comments(sync: ModuleType, *, repo: str, number: int) -> list[dict]:
    """Fetch issue comments used as release close-out evidence."""

    raw = sync._run_gh(
        ["issue", "view", str(number), "--repo", repo, "--json", "comments"],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise SystemExit("Unexpected GitHub CLI issue view response.")
    comments = raw.get("comments", [])
    if not isinstance(comments, list):
        return []
    return [comment for comment in comments if isinstance(comment, dict)]


def close_issue(sync: ModuleType, *, repo: str, issue: dict, comment: str) -> None:
    """Comment on and close one release-labeled backlog issue."""

    number = str(issue["number"])
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file_obj:
        file_obj.write(comment)
        body_path = Path(file_obj.name)
    try:
        sync._run_gh(
            [
                "issue",
                "comment",
                number,
                "--repo",
                repo,
                "--body-file",
                str(body_path),
            ]
        )
        sync._run_gh(["issue", "close", number, "--repo", repo])
    finally:
        body_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    sync = _load_sync_script()
    parser = argparse.ArgumentParser(
        description="Close open backlog issues labeled for a published release."
    )
    parser.add_argument(
        "--repo",
        default=sync.DEFAULT_REPO,
        help="GitHub repository in owner/name form.",
    )
    parser.add_argument("--release", required=True, help="Release tag, for example v0.4.0.")
    parser.add_argument("--dry-run", action="store_true", help="Print issues that would be closed.")
    parser.add_argument("--limit", type=int, default=200, help="Maximum issues to inspect.")
    args = parser.parse_args(argv)

    if not sync.RELEASE_RE.fullmatch(args.release) or args.release == "unscheduled":
        sys.stderr.write("--release must be a concrete release tag such as v0.4.0.\n")
        return 1
    if args.limit < 1 or args.limit > MAX_ISSUE_LIMIT:
        sys.stderr.write(f"--limit must be between 1 and {MAX_ISSUE_LIMIT}.\n")
        return 1

    comment = render_close_comment(args.release)
    sync.validate_public_issue_text(comment, field="release close comment")

    if not args.dry_run:
        sync.check_gh_auth()
        sync._run_gh(["release", "view", args.release, "--repo", args.repo])

    issues = list_released_backlog_issues(
        sync,
        repo=args.repo,
        release=args.release,
        limit=args.limit,
    )
    managed_issues: list[dict] = []
    for issue in issues:
        ready, reason = release_close_ready(issue, sync.MARKER_PREFIX)
        if ready:
            managed_issues.append(issue)
            continue
        number = issue["number"]
        title = issue["title"]
        sys.stdout.write(f"skipping issue #{number}: {title} ({reason})\n")

    for issue in managed_issues:
        number = issue["number"]
        title = issue["title"]
        if args.dry_run:
            sys.stdout.write(f"would close issue #{number}: {title}\n")
            continue
        close_issue(sync, repo=args.repo, issue=issue, comment=comment)

    if not managed_issues:
        sys.stdout.write(f"No open managed backlog issues found for release {args.release}.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
