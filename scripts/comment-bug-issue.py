# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Post sanitized TDD lifecycle comments to managed bug reports."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BUG_SYNC_SCRIPT = SCRIPT_DIR / "sync-bug-reports.py"
UNSCHEDULED_RELEASE_LABEL = "release-unscheduled"
COMPLETED_LABEL = "completed"
COMPLETED_STATUSES = frozenset({"completed", "closeout", "released"})
MAX_ATTACHED_TEST_BYTES = 20_000
CODE_FENCE_BY_SUFFIX = {
    ".bash": "bash",
    ".json": "json",
    ".md": "markdown",
    ".py": "python",
    ".sh": "sh",
    ".toml": "toml",
    ".txt": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zsh": "sh",
}
LIFECYCLE_REQUIRED_SECTIONS = {
    "failing-test": ("Failing Test", "Reproduction Evidence", "Expected Failure"),
    "started": ("Planned Fix", "Test Driven Plan", "Documentation And Release Notes"),
    "completed": (
        "Completed Fix",
        "Acceptance Criteria",
        "Regression Test Evidence",
        "Verification Evidence",
        "Close-Out Evidence",
    ),
    "closeout": (
        "Completed Fix",
        "Acceptance Criteria",
        "Regression Test Evidence",
        "Verification Evidence",
        "Close-Out Evidence",
    ),
    "released": (
        "Completed Fix",
        "Acceptance Criteria",
        "Regression Test Evidence",
        "Verification Evidence",
        "Close-Out Evidence",
    ),
}


def _load_bug_sync_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_bug_reports", BUG_SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit("Unable to load scripts/sync-bug-reports.py.")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_bug_reports"] = module
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


def _safe_relative_test_path(path: Path) -> Path:
    """Return a repository-relative test or script path safe for public comments."""

    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SystemExit(f"Unable to read test file {path}.") from exc
    try:
        relative = resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise SystemExit("--test-file must be inside this repository.") from exc
    if relative.parts[0] not in {"tests", "scripts"}:
        raise SystemExit("--test-file must point to a file under tests/ or scripts/.")
    return relative


def _read_test_file(sync: ModuleType, path: Path) -> str:
    """Read a small committed regression test for sanitized bug comments."""

    relative = _safe_relative_test_path(path)
    resolved = REPO_ROOT / relative
    content = resolved.read_text(encoding="utf-8")
    encoded_size = len(content.encode("utf-8"))
    if encoded_size > MAX_ATTACHED_TEST_BYTES:
        raise SystemExit("--test-file is too large to include in a public issue comment.")
    sync.validate_public_issue_text(str(relative), field="test file path")
    sync.validate_public_issue_text(content, path=relative, field="test file content")
    language = CODE_FENCE_BY_SUFFIX.get(relative.suffix.casefold(), "text")
    return f"""## Attached Regression Test Script

Path: `{relative}`

```{language}
{content.rstrip()}
```
"""


def _has_markdown_heading(text: str, heading: str) -> bool:
    wanted = heading.casefold()
    for line in text.splitlines():
        normalized = line.strip().lstrip("#").strip().casefold()
        if normalized == wanted:
            return True
    return False


def validate_lifecycle_comment(text: str, *, status: str) -> None:
    """Validate required section headings for TDD bug lifecycle notes."""

    required = LIFECYCLE_REQUIRED_SECTIONS.get(status, ())
    missing = [section for section in required if not _has_markdown_heading(text, section)]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"comment for status {status!r} is missing section(s): {joined}")


def acceptance_checklist_complete(body: str) -> str:
    """Check every Acceptance Criteria checklist item in a managed bug body."""

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
    raw = sync._run_gh(
        ["issue", "view", str(number), "--repo", repo, "--json", "body"],
        capture_json=True,
    )
    if not isinstance(raw, dict):
        raise SystemExit("Unexpected GitHub CLI issue view response.")
    return str(raw.get("body", ""))


def _issue_labels(sync: ModuleType, *, repo: str, number: int) -> set[str]:
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


def _build_parser(bug_sync: ModuleType) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Post a sanitized TDD lifecycle comment to a managed bug issue."
    )
    parser.add_argument("--repo", default=bug_sync._load_sync_script().DEFAULT_REPO)
    parser.add_argument("--bug-id", required=True, help="Managed bug identifier.")
    parser.add_argument("--release", required=True, help="Release tag, for example v0.4.0.")
    parser.add_argument(
        "--status",
        choices=(
            "failing-test",
            "started",
            "progress",
            "blocked",
            "completed",
            "closeout",
            "released",
        ),
        default="progress",
        help="Kind of bug lifecycle note being posted.",
    )
    parser.add_argument("--assignee", default=bug_sync.DEFAULT_ASSIGNEE)
    parser.add_argument("--comment-file", type=Path, required=True)
    parser.add_argument(
        "--test-file",
        type=Path,
        help="Attach a small sanitized regression test file to a failing-test comment.",
    )
    parser.add_argument("--complete-acceptance", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--close-released", action="store_true")
    return parser


def _validated_comment(
    bug_sync: ModuleType, sync: ModuleType, args: argparse.Namespace
) -> str | None:
    if not sync.ID_RE.fullmatch(args.bug_id):
        sys.stderr.write("--bug-id must use lowercase letters, numbers, and hyphens.\n")
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

    attached_test = ""
    if args.test_file is not None:
        if args.status != "failing-test":
            sys.stderr.write("--test-file can only be used with --status failing-test.\n")
            return None
        attached_test = f"\n\n{_read_test_file(sync, args.test_file)}"

    body = f"""## Bug Work Note

Status: `{args.status}`
Target release: `{args.release}`

{comment_text}{attached_test}
"""
    sync.validate_public_issue_text(body, field="rendered comment")
    _ = bug_sync
    return body


def _assign_issue(sync: ModuleType, args: argparse.Namespace, issue_number: int) -> None:
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
    if UNSCHEDULED_RELEASE_LABEL not in labels:
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
    """Mark fixed bugs as completed while release-gated closure remains pending."""

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
    if not args.complete_acceptance:
        return
    updated_body = acceptance_checklist_complete(
        _issue_body(sync, repo=args.repo, number=issue_number)
    )
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
    bug_sync = _load_bug_sync_script()
    sync = bug_sync._load_sync_script()
    parser = _build_parser(bug_sync)
    args = parser.parse_args(argv)
    body = _validated_comment(bug_sync, sync, args)
    if body is None:
        return 1

    if args.dry_run:
        sys.stdout.write(f"would comment on bug {args.bug_id} for {args.release}\n")
        if args.assignee:
            sys.stdout.write(f"would assign issue to {args.assignee}\n")
        if args.complete_acceptance:
            sys.stdout.write("would mark Acceptance Criteria checklist items complete\n")
        if args.status in COMPLETED_STATUSES:
            sys.stdout.write(f"would add label {COMPLETED_LABEL!r}\n")
        return 0

    sync.check_gh_auth()
    issue = bug_sync.existing_issue(sync, args.repo, args.bug_id)
    if issue is None:
        sys.stderr.write(f"No GitHub issue found for bug ID {args.bug_id}.\n")
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
