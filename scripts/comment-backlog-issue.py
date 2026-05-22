# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Post sanitized implementation notes to backlog issues.

Feature requests in this repository are intentionally public release-planning
records. This helper keeps the workflow repeatable when maintainers start work,
post progress, or close an issue after a release exists. It reuses the backlog
sync validator so comments cannot accidentally include URLs, IP addresses,
credential assignments, tokens, or certificate material.
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
UNSCHEDULED_RELEASE_LABEL = "release-unscheduled"
COMPLETED_LABEL = "completed"
COMPLETED_STATUSES = frozenset({"completed", "closeout", "released"})
LIFECYCLE_REQUIRED_SECTIONS = {
    "started": ("Planned Work", "Test Plan", "Documentation And Release Notes"),
    "completed": (
        "Completed Work",
        "Acceptance Criteria",
        "Test Plan Evidence",
        "Close-Out Evidence",
    ),
    "closeout": (
        "Completed Work",
        "Acceptance Criteria",
        "Test Plan Evidence",
        "Close-Out Evidence",
    ),
    "released": (
        "Completed Work",
        "Acceptance Criteria",
        "Test Plan Evidence",
        "Close-Out Evidence",
    ),
}


def _load_sync_script() -> ModuleType:
    """Load the sync helper even though its file name contains hyphens."""

    spec = importlib.util.spec_from_file_location("sync_backlog_issues", SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit("Unable to load scripts/sync-backlog-issues.py.")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_backlog_issues"] = module
    spec.loader.exec_module(module)
    return module


def _release_label(target_release: str) -> str:
    return f"release-{target_release}"


def _read_comment(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Unable to read comment file {path}.") from exc
    normalized = text.strip()
    if not normalized:
        raise SystemExit("Comment file must not be empty.")
    return normalized


def _has_markdown_heading(text: str, heading: str) -> bool:
    """Return true when comment text contains a specific Markdown heading.

    Backlog comments are public release evidence. Requiring named sections keeps
    implementation notes consistent enough for maintainers, auditors, and
    future release automation to understand what happened without inspecting
    private chat history or local terminal output.
    """

    wanted = heading.casefold()
    for line in text.splitlines():
        normalized = line.strip().lstrip("#").strip().casefold()
        if normalized == wanted:
            return True
    return False


def validate_lifecycle_comment(text: str, *, status: str) -> None:
    """Validate required section headings for public backlog lifecycle notes."""

    required = LIFECYCLE_REQUIRED_SECTIONS.get(status, ())
    missing = [section for section in required if not _has_markdown_heading(text, section)]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"comment for status {status!r} is missing section(s): {joined}")


def acceptance_checklist_complete(body: str) -> str:
    """Return an issue body with Acceptance Criteria checklist items checked.

    The helper edits only the `## Acceptance Criteria` section. It avoids broad
    Markdown rewriting so release evidence remains stable and reviewable.
    """

    lines = body.splitlines()
    in_acceptance = False
    saw_acceptance_item = False
    changed = False
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_acceptance = stripped == "## Acceptance Criteria"
        if in_acceptance:
            left = line.lstrip()
            prefix = line[: len(line) - len(left)]
            if left.startswith("- [ ] "):
                updated.append(f"{prefix}- [x] {left[6:]}")
                saw_acceptance_item = True
                changed = True
                continue
            if left.lower().startswith("- [x] "):
                saw_acceptance_item = True
        updated.append(line)

    if not saw_acceptance_item:
        raise ValueError("issue body does not contain Acceptance Criteria checklist items")

    rendered = "\n".join(updated)
    if body.endswith("\n"):
        rendered += "\n"
    return rendered if changed else body


def _issue_body(sync: ModuleType, *, repo: str, number: int) -> str:
    """Fetch the current issue body for acceptance-checklist updates."""

    raw = sync._run_gh(
        ["issue", "view", str(number), "--repo", repo, "--json", "body"],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise SystemExit("Unexpected GitHub CLI issue view response.")
    return str(raw.get("body", ""))


def _issue_labels(sync: ModuleType, *, repo: str, number: int) -> set[str]:
    """Fetch current label names so stale release labels can be removed safely."""

    raw = sync._run_gh(
        ["issue", "view", str(number), "--repo", repo, "--json", "labels"],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise SystemExit("Unexpected GitHub CLI issue view response.")
    labels = raw.get("labels", [])
    if not isinstance(labels, list):
        return set()
    return {
        str(label.get("name", ""))
        for label in labels
        if isinstance(label, dict) and label.get("name")
    }


def _build_parser(sync: ModuleType) -> argparse.ArgumentParser:
    """Build the command-line parser for the lifecycle helper."""

    parser = argparse.ArgumentParser(
        description="Post a sanitized progress or close-out comment to a backlog issue."
    )
    parser.add_argument("--repo", default=sync.DEFAULT_REPO, help="GitHub repository.")
    parser.add_argument("--backlog-id", required=True, help="Backlog item identifier.")
    parser.add_argument("--release", required=True, help="Release tag, for example v0.4.0.")
    parser.add_argument(
        "--status",
        choices=("started", "progress", "blocked", "completed", "closeout", "released"),
        default="progress",
        help="Kind of implementation note being posted.",
    )
    parser.add_argument("--assignee", help="GitHub username to assign before posting.")
    parser.add_argument(
        "--complete-acceptance",
        action="store_true",
        help="Mark every Acceptance Criteria checklist item as complete in the issue body.",
    )
    parser.add_argument("--comment-file", type=Path, required=True, help="Comment Markdown file.")
    parser.add_argument("--dry-run", action="store_true", help="Validate without GitHub writes.")
    parser.add_argument(
        "--close-released",
        action="store_true",
        help="Close after verifying that the named GitHub Release exists.",
    )
    return parser


def _validated_comment(sync: ModuleType, args: argparse.Namespace) -> str | None:
    """Return a rendered comment body, or None after printing a validation error."""

    if not sync.ID_RE.fullmatch(args.backlog_id):
        sys.stderr.write("--backlog-id must use lowercase letters, numbers, and hyphens.\n")
        return None
    if not sync.RELEASE_RE.fullmatch(args.release) or args.release == "unscheduled":
        sys.stderr.write("--release must be a concrete release tag such as v0.4.0.\n")
        return None

    comment_text = _read_comment(args.comment_file)
    try:
        validate_lifecycle_comment(comment_text, status=args.status)
        sync.validate_public_issue_text(comment_text, path=args.comment_file, field="comment")
    except (ValueError, sync.BacklogValidationError) as exc:
        sys.stderr.write(f"{exc}\n")
        return None

    body = f"""## Backlog Work Note

Status: `{args.status}`
Target release: `{args.release}`

{comment_text}
"""
    sync.validate_public_issue_text(body, field="rendered comment")
    return body


def _print_dry_run(args: argparse.Namespace) -> None:
    """Print the planned action without writing to GitHub."""

    sys.stdout.write(
        f"would comment on backlog {args.backlog_id} for {args.release} with status {args.status}\n"
    )
    if args.assignee:
        sys.stdout.write(f"would assign issue to {args.assignee}\n")
    if args.complete_acceptance:
        sys.stdout.write("would mark Acceptance Criteria checklist items complete\n")
    if args.status in COMPLETED_STATUSES:
        sys.stdout.write(f"would add label {COMPLETED_LABEL!r}\n")


def _assign_issue(sync: ModuleType, args: argparse.Namespace, issue_number: int) -> None:
    """Assign an issue when an assignee was provided."""

    if not args.assignee:
        return
    sync._run_gh(
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            args.repo,
            "--add-assignee",
            args.assignee,
        ]
    )


def _apply_release_label(sync: ModuleType, args: argparse.Namespace, issue_number: int) -> None:
    """Apply the target release label and remove the unscheduled label if present."""

    sync._run_gh(
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            args.repo,
            "--add-label",
            _release_label(args.release),
        ]
    )
    labels = _issue_labels(sync, repo=args.repo, number=issue_number)
    if args.release == "unscheduled" or UNSCHEDULED_RELEASE_LABEL not in labels:
        return
    sync._run_gh(
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            args.repo,
            "--remove-label",
            UNSCHEDULED_RELEASE_LABEL,
        ]
    )


def _apply_completed_label(sync: ModuleType, args: argparse.Namespace, issue_number: int) -> None:
    """Mark locally completed work while keeping the issue open for release.

    GitHub Issues are the live backlog. Once implementation evidence is posted,
    maintainers need a visible state that says "done in development, not yet
    released". The `completed` label provides that state without bypassing the
    release-gated closure scripts.
    """

    if args.status not in COMPLETED_STATUSES:
        return
    sync.ensure_labels(args.repo, [COMPLETED_LABEL], dry_run=False)
    sync._run_gh(
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            args.repo,
            "--add-label",
            COMPLETED_LABEL,
        ]
    )


def _complete_acceptance(sync: ModuleType, args: argparse.Namespace, issue_number: int) -> None:
    """Tick all Acceptance Criteria boxes in the issue body when requested."""

    if not args.complete_acceptance:
        return
    current_body = _issue_body(sync, repo=args.repo, number=issue_number)
    updated_body = acceptance_checklist_complete(current_body)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file_obj:
        file_obj.write(updated_body)
        issue_body_path = Path(file_obj.name)
    try:
        sync._run_gh(
            [
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                args.repo,
                "--body-file",
                str(issue_body_path),
            ]
        )
    finally:
        issue_body_path.unlink(missing_ok=True)


def _post_comment(sync: ModuleType, args: argparse.Namespace, issue_number: int, body: str) -> None:
    """Post the already-validated lifecycle comment to the GitHub issue."""

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file_obj:
        file_obj.write(body)
        body_path = Path(file_obj.name)
    try:
        sync._run_gh(
            [
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                args.repo,
                "--body-file",
                str(body_path),
            ]
        )
    finally:
        body_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    sync = _load_sync_script()
    parser = _build_parser(sync)
    args = parser.parse_args(argv)
    body = _validated_comment(sync, args)
    if body is None:
        return 1

    if args.dry_run:
        _print_dry_run(args)
        return 0

    sync.check_gh_auth()
    issue = sync.existing_issue(args.repo, args.backlog_id)
    if issue is None:
        sys.stderr.write(f"No GitHub issue found for backlog ID {args.backlog_id}.\n")
        return 1

    _assign_issue(sync, args, issue.number)
    _post_comment(sync, args, issue.number, body)
    _apply_release_label(sync, args, issue.number)
    _apply_completed_label(sync, args, issue.number)
    _complete_acceptance(sync, args, issue.number)
    if args.close_released:
        sync._run_gh(["release", "view", args.release, "--repo", args.repo])
        sync._run_gh(["issue", "close", str(issue.number), "--repo", args.repo])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
