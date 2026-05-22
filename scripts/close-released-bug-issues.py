# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Close release-labeled managed bug reports after a GitHub Release exists."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

SCRIPT_DIR = Path(__file__).resolve().parent
BUG_SYNC_SCRIPT = SCRIPT_DIR / "sync-bug-reports.py"
MAX_ISSUE_LIMIT = 500


def _load_bug_sync_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_bug_reports", BUG_SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit("Unable to load scripts/sync-bug-reports.py.")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_bug_reports"] = module
    spec.loader.exec_module(module)
    return module


def has_bug_marker(body: str, marker_prefix: str) -> bool:
    """Return true when an issue body carries the managed bug marker."""

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


def has_bug_closeout_evidence(comments: object) -> bool:
    """Return true when a bug has regression and verification evidence."""

    if not isinstance(comments, list):
        return False
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body", ""))
        if (
            "Regression Test Evidence" in body
            and "Verification Evidence" in body
            and "Close-Out Evidence" in body
        ):
            return True
    return False


def release_close_ready(issue: dict, marker_prefix: str) -> tuple[bool, str]:
    """Validate that a release-labeled bug report is safe to close."""

    body = str(issue.get("body", ""))
    if not has_bug_marker(body, marker_prefix):
        return False, "missing managed bug marker"
    if not acceptance_criteria_complete(body):
        return False, "acceptance criteria are not fully checked"
    if not has_bug_closeout_evidence(issue.get("comments", [])):
        return False, "missing regression and close-out evidence comment"
    return True, "ready"


def render_close_comment(release: str) -> str:
    """Render a safe release close-out comment for a managed bug."""

    return f"""## Release Bug Close-Out

Released in `{release}`.

This bug report is being closed by release automation after the GitHub Release
was created. The issue already contains the failing-test evidence, the fix
summary, regression-test evidence, verification evidence, and release notes
prepared for this release.

If the problem reappears, open a new bug report with a fresh failing test.
"""


def issue_body(sync: ModuleType, *, repo: str, number: int) -> str:
    raw = sync._run_gh(
        ["issue", "view", str(number), "--repo", repo, "--json", "body"],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise SystemExit("Unexpected GitHub CLI issue view response.")
    return str(raw.get("body", ""))


def issue_comments(sync: ModuleType, *, repo: str, number: int) -> list[dict]:
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


def list_released_bug_issues(
    sync: ModuleType, *, repo: str, release: str, limit: int
) -> list[dict]:
    """List open managed bugs labeled for a release."""

    raw = sync._run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--label",
            "bug",
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


def close_issue(sync: ModuleType, *, repo: str, issue: dict, comment: str) -> None:
    """Comment on and close one release-labeled managed bug."""

    number = str(issue["number"])
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file_obj:
        file_obj.write(comment)
        body_path = Path(file_obj.name)
    try:
        sync._run_gh(["issue", "comment", number, "--repo", repo, "--body-file", str(body_path)])
        sync._run_gh(["issue", "close", number, "--repo", repo])
    finally:
        body_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    bug_sync = _load_bug_sync_script()
    sync = bug_sync._load_sync_script()
    parser = argparse.ArgumentParser(
        description="Close open managed bug reports labeled for a published release."
    )
    parser.add_argument("--repo", default=sync.DEFAULT_REPO)
    parser.add_argument("--release", required=True, help="Release tag, for example v0.4.0.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args(argv)

    if not sync.RELEASE_RE.fullmatch(args.release) or args.release == "unscheduled":
        sys.stderr.write("--release must be a concrete release tag such as v0.4.0.\n")
        return 1
    if args.limit < 1 or args.limit > MAX_ISSUE_LIMIT:
        sys.stderr.write(f"--limit must be between 1 and {MAX_ISSUE_LIMIT}.\n")
        return 1

    comment = render_close_comment(args.release)
    sync.validate_public_issue_text(comment, field="release bug close comment")

    if not args.dry_run:
        sync.check_gh_auth()
        sync._run_gh(["release", "view", args.release, "--repo", args.repo])

    issues = list_released_bug_issues(sync, repo=args.repo, release=args.release, limit=args.limit)
    managed_issues: list[dict] = []
    for issue in issues:
        ready, reason = release_close_ready(issue, bug_sync.MARKER_PREFIX)
        if ready:
            managed_issues.append(issue)
            continue
        sys.stdout.write(f"skipping issue #{issue['number']}: {issue['title']} ({reason})\n")

    for issue in managed_issues:
        if args.dry_run:
            sys.stdout.write(f"would close issue #{issue['number']}: {issue['title']}\n")
            continue
        close_issue(sync, repo=args.repo, issue=issue, comment=comment)

    if not managed_issues:
        sys.stdout.write(f"No open managed bug issues found for release {args.release}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
