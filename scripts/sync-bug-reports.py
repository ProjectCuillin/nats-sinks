# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Sync sanitized local bug reports to GitHub Issues.

Bug reports are public defect records. This helper mirrors the backlog sync
workflow, but it keeps defects separate from feature requests by using a
dedicated hidden marker, bug-specific labels, and a TDD-focused issue body.
Every public field is validated with the same leak-prevention rules used by the
backlog tooling before the GitHub CLI can publish it.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import github_issue_planning  # noqa: E402 - scripts directory is added before import.

SYNC_SCRIPT = SCRIPT_DIR / "sync-backlog-issues.py"
DEFAULT_DIRECTORY = Path("bugs/reports")
MARKER_PREFIX = "nats-sinks-bug-id:"
DEFAULT_LABELS = ("bug",)
DEFAULT_ASSIGNEE = "louwersj"
MAX_TEXT = 4_000

SEVERITIES = {
    "critical": "severity-critical",
    "high": "severity-high",
    "medium": "severity-medium",
    "low": "severity-low",
}


@dataclass(frozen=True)
class BugReport:
    """Validated local representation of a managed GitHub bug report."""

    identifier: str
    title: str
    area: str
    severity: str
    priority: str
    target_release: str
    labels: tuple[str, ...]
    summary: str
    observed: str
    expected: str
    reproduction: str
    failing_test: str
    impact: str
    delivery_semantics: str
    security: str
    acceptance: tuple[str, ...]
    tests: str
    documentation: str
    closeout: str
    relationships: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class ExistingIssue:
    """Small subset of GitHub issue data needed for idempotent bug sync."""

    number: int
    state: str
    title: str
    url: str
    body: str
    labels: tuple[str, ...]


class BugReportValidationError(ValueError):
    """Raised when a local bug report JSON file is malformed or unsafe."""


def _load_sync_script() -> ModuleType:
    """Load shared public-safety validators from the backlog sync script."""

    spec = importlib.util.spec_from_file_location("sync_backlog_issues", SYNC_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit("Unable to load scripts/sync-backlog-issues.py.")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_backlog_issues"] = module
    spec.loader.exec_module(module)
    return module


def _as_mapping(value: object, *, path: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BugReportValidationError(f"{path}: root JSON value must be an object.")
    return value


def _required_text(
    sync: ModuleType, data: dict[str, object], key: str, *, path: Path, max_length: int = MAX_TEXT
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BugReportValidationError(f"{path}: {key!r} must be a non-empty string.")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise BugReportValidationError(f"{path}: {key!r} is too long.")
    try:
        sync.validate_public_issue_text(normalized, path=path, field=key)
    except sync.BacklogValidationError as exc:
        raise BugReportValidationError(str(exc)) from exc
    return normalized


def _optional_labels(sync: ModuleType, data: dict[str, object], *, path: Path) -> tuple[str, ...]:
    value = data.get("labels", [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BugReportValidationError(f"{path}: 'labels' must be a list of strings.")

    labels: list[str] = [*DEFAULT_LABELS]
    for label in value:
        normalized = label.strip()
        if not normalized:
            continue
        if not sync.LABEL_RE.fullmatch(normalized):
            raise BugReportValidationError(f"{path}: invalid label {normalized!r}.")
        try:
            sync.validate_public_issue_text(normalized, path=path, field="labels")
        except sync.BacklogValidationError as exc:
            raise BugReportValidationError(str(exc)) from exc
        labels.append(normalized)
    return tuple(dict.fromkeys(labels))


def _target_release(sync: ModuleType, data: dict[str, object], *, path: Path) -> str:
    value = data.get("target_release", "unscheduled")
    if not isinstance(value, str) or not value.strip():
        raise BugReportValidationError(f"{path}: 'target_release' must be a non-empty string.")
    normalized = value.strip()
    if not sync.RELEASE_RE.fullmatch(normalized):
        raise BugReportValidationError(
            f"{path}: 'target_release' must be 'unscheduled' or a version tag like v1.2.3."
        )
    try:
        sync.validate_public_issue_text(normalized, path=path, field="target_release")
    except sync.BacklogValidationError as exc:
        raise BugReportValidationError(str(exc)) from exc
    return normalized


def _severity_label(severity: str, *, path: Path) -> str:
    try:
        return SEVERITIES[severity]
    except KeyError as exc:
        allowed = ", ".join(sorted(SEVERITIES))
        raise BugReportValidationError(f"{path}: 'severity' must be one of: {allowed}.") from exc


def _acceptance(sync: ModuleType, data: dict[str, object], *, path: Path) -> tuple[str, ...]:
    value = data.get("acceptance")
    if not isinstance(value, list) or not value:
        raise BugReportValidationError(f"{path}: 'acceptance' must be a non-empty list.")
    result: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise BugReportValidationError(f"{path}: acceptance item {index} must be text.")
        normalized = item.strip()
        if len(normalized) > sync.ACCEPTANCE_ITEM_MAX_LENGTH:
            raise BugReportValidationError(f"{path}: acceptance item {index} is too long.")
        try:
            sync.validate_public_issue_text(normalized, path=path, field=f"acceptance[{index}]")
        except sync.BacklogValidationError as exc:
            raise BugReportValidationError(str(exc)) from exc
        result.append(normalized)
    return tuple(result)


def load_bug_report(path: Path) -> BugReport:
    """Load and validate one local bug report JSON file."""

    sync = _load_sync_script()
    try:
        data = sync.load_local_json(path)
    except sync.BacklogValidationError as exc:
        raise BugReportValidationError(str(exc)) from exc

    identifier = _required_text(sync, data, "id", path=path, max_length=100)
    if not sync.ID_RE.fullmatch(identifier):
        raise BugReportValidationError(
            f"{path}: 'id' must use lowercase letters, numbers, and hyphens."
        )

    area = _required_text(sync, data, "area", path=path, max_length=80)
    if area not in sync.AREAS:
        raise BugReportValidationError(f"{path}: unsupported area {area!r}.")

    priority = _required_text(sync, data, "priority", path=path, max_length=80)
    if priority not in sync.PRIORITIES:
        raise BugReportValidationError(f"{path}: unsupported priority {priority!r}.")

    severity = _required_text(sync, data, "severity", path=path, max_length=20).casefold()
    target_release = _target_release(sync, data, path=path)
    labels = (
        *_optional_labels(sync, data, path=path),
        f"release-{target_release}",
        _severity_label(severity, path=path),
    )

    return BugReport(
        identifier=identifier,
        title=_required_text(sync, data, "title", path=path, max_length=180),
        area=area,
        severity=severity,
        priority=priority,
        target_release=target_release,
        labels=tuple(dict.fromkeys(labels)),
        summary=_required_text(sync, data, "summary", path=path),
        observed=_required_text(sync, data, "observed", path=path),
        expected=_required_text(sync, data, "expected", path=path),
        reproduction=_required_text(sync, data, "reproduction", path=path),
        failing_test=_required_text(sync, data, "failing_test", path=path),
        impact=_required_text(sync, data, "impact", path=path),
        delivery_semantics=_required_text(sync, data, "delivery_semantics", path=path),
        security=_required_text(sync, data, "security", path=path),
        acceptance=_acceptance(sync, data, path=path),
        tests=_required_text(sync, data, "tests", path=path),
        documentation=_required_text(sync, data, "documentation", path=path),
        closeout=_required_text(sync, data, "closeout", path=path),
        relationships=sync._relationships(data, path=path),
    )


def discover_reports(directory: Path) -> list[BugReport]:
    """Return validated bug reports sorted by identifier."""

    if not directory.exists():
        return []
    paths = sorted(path for path in directory.glob("*.json") if path.is_file())
    reports = [load_bug_report(path) for path in paths]
    identifiers = [report.identifier for report in reports]
    duplicates = sorted(
        identifier for identifier, count in Counter(identifiers).items() if count > 1
    )
    if duplicates:
        joined = ", ".join(duplicates)
        raise BugReportValidationError(f"Duplicate bug identifiers: {joined}")
    return sorted(reports, key=lambda report: report.identifier)


def render_issue_body(report: BugReport) -> str:
    """Render a best-practice public bug report body."""

    acceptance = "\n".join(f"- [ ] {entry}" for entry in report.acceptance)
    labels = ", ".join(f"`{label}`" for label in report.labels)
    relationships = sync_backlog_issue_relationships(report.relationships)
    return f"""<!-- {MARKER_PREFIX} {report.identifier} -->

## Summary

{report.summary}

## Observed Behavior

{report.observed}

## Expected Behavior

{report.expected}

## Minimal Reproduction

{report.reproduction}

## Failing Regression Test

{report.failing_test}

## Impact And Affected Area

{report.impact}

## Delivery Semantics And Idempotency Impact

{report.delivery_semantics}

## Security And Privacy Considerations

{report.security}

## Acceptance Criteria

{acceptance}

## Test Plan

{report.tests}

## Documentation And Release-Note Plan

{report.documentation}

## Close-Out Evidence Required

{report.closeout}

## Issue Relationships

{relationships}

## Triage Metadata

- Bug ID: `{report.identifier}`
- Area: `{report.area}`
- Severity: `{report.severity}`
- Priority: `{report.priority}`
- Target release: `{report.target_release}`
- Labels: {labels}
"""


def sync_backlog_issue_relationships(relationships: Mapping[str, tuple[str, ...]]) -> str:
    """Render issue relationships with the same wording as backlog items."""

    sync = _load_sync_script()
    return sync._render_relationships(relationships)


def existing_issue(sync: ModuleType, repo: str, identifier: str) -> ExistingIssue | None:
    """Find an existing managed bug issue by the hidden marker."""

    marker = f"{MARKER_PREFIX} {identifier}"
    search = f'"{MARKER_PREFIX} {identifier}" in:body repo:{repo}'
    raw = sync._run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--search",
            search,
            "--json",
            "number,state,title,url,body,labels",
            "--limit",
            "5",
        ],
        capture_json=True,
    )
    if not isinstance(raw, list):
        raise SystemExit("Unexpected GitHub CLI issue list response.")
    for issue in raw:
        body = str(issue.get("body", "")) if isinstance(issue, dict) else ""
        if marker not in body:
            continue
        return ExistingIssue(
            number=int(issue["number"]),
            state=str(issue["state"]),
            title=str(issue["title"]),
            url=str(issue["url"]),
            body=body,
            labels=sync._issue_label_names(issue),
        )
    return None


def _issue_args(
    repo: str, report: BugReport, body_path: Path, *, assignee: str | None
) -> list[str]:
    args = ["--repo", repo, "--title", report.title, "--body-file", str(body_path)]
    for label in report.labels:
        args.extend(["--label", label])
    if assignee:
        args.extend(["--assignee", assignee])
    return args


def _assign(sync: ModuleType, repo: str, issue_number: int, assignee: str | None) -> None:
    if not assignee:
        return
    sync._run_gh(
        [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repo,
            "--add-assignee",
            assignee,
        ]
    )


def _sync_planning(
    sync: ModuleType,
    repo: str,
    report: BugReport,
    issue: ExistingIssue,
    *,
    dry_run: bool,
    issue_fields: github_issue_planning.IssueFieldPlanningConfig | None,
) -> None:
    """Update required GitHub-native planning metadata for one bug issue."""

    if issue_fields is None and not report.relationships:
        return
    github_issue_planning.sync_issue_planning(
        repo=repo,
        issue=github_issue_planning.IssuePlanningSpec(
            number=issue.number,
            url=issue.url,
            priority=report.priority,
            relationships=report.relationships,
        ),
        issue_fields=issue_fields,
        dry_run=dry_run,
    )


def sync_report(  # noqa: PLR0912 - mirrors the create/update lifecycle explicitly.
    sync: ModuleType,
    repo: str,
    report: BugReport,
    *,
    assignee: str | None,
    dry_run: bool,
    reopen_closed: bool,
    sync_planning: bool,
    issue_fields: github_issue_planning.IssueFieldPlanningConfig | None,
) -> None:
    """Create or update one managed GitHub bug report."""

    body = render_issue_body(report)
    sync.validate_public_issue_text(body, field="bug issue body")
    if dry_run:
        sys.stdout.write(f"would sync {report.identifier}: {report.title}\n")
        if assignee:
            sys.stdout.write(f"would assign issue to {assignee}\n")
        if sync_planning:
            if issue_fields is not None:
                sys.stdout.write(
                    "would sync GitHub Issue priority field for this issue after creation/update\n"
                )
            if report.relationships:
                sys.stdout.write("would sync declared GitHub issue relationships\n")
        return

    found = existing_issue(sync, repo, report.identifier)
    if found and found.state.upper() == "CLOSED" and not reopen_closed:
        sys.stdout.write(f"skipping closed bug #{found.number}: {found.url}\n")
        return
    if found is not None:
        body = sync.preserve_acceptance_checks(body, found.body)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file_obj:
        file_obj.write(body)
        body_path = Path(file_obj.name)

    try:
        if found is None:
            sync._run_gh(
                ["issue", "create", *_issue_args(repo, report, body_path, assignee=assignee)]
            )
            if sync_planning:
                created = existing_issue(sync, repo, report.identifier)
                if created is not None:
                    _sync_planning(
                        sync,
                        repo,
                        report,
                        created,
                        dry_run=False,
                        issue_fields=issue_fields,
                    )
            return

        if found.state.upper() == "CLOSED" and reopen_closed:
            sync._run_gh(["issue", "reopen", str(found.number), "--repo", repo])

        edit_args = [
            "issue",
            "edit",
            str(found.number),
            "--repo",
            repo,
            "--title",
            report.title,
            "--body-file",
            str(body_path),
        ]
        for label in report.labels:
            edit_args.extend(["--add-label", label])
        sync._run_gh(edit_args)
        sync._remove_labels(
            repo,
            found.number,
            sync._legacy_priority_labels_present(found.labels),
            dry_run=False,
        )
        _assign(sync, repo, found.number, assignee)
        if sync_planning:
            _sync_planning(sync, repo, report, found, dry_run=False, issue_fields=issue_fields)
    finally:
        body_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    sync = _load_sync_script()
    parser = argparse.ArgumentParser(
        description="Sync sanitized local bug report JSON files to GitHub Issues."
    )
    parser.add_argument("--repo", default=sync.DEFAULT_REPO, help="GitHub repository.")
    parser.add_argument(
        "--directory",
        type=Path,
        default=DEFAULT_DIRECTORY,
        help="Directory containing local bug report JSON files.",
    )
    parser.add_argument(
        "--assignee",
        default=DEFAULT_ASSIGNEE,
        help="GitHub username assigned to synced bug reports. Use an empty value to skip.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate without GitHub writes.")
    parser.add_argument("--check", action="store_true", help="Validate local bug reports only.")
    parser.add_argument("--reopen-closed", action="store_true", help="Reopen matching closed bugs.")
    parser.add_argument("--skip-label-sync", action="store_true", help="Do not create labels.")
    parser.add_argument(
        "--skip-planning-sync",
        action="store_true",
        help=(
            "Dry-run/local escape hatch; live sync refuses this because "
            "GitHub Issue priority is required."
        ),
    )
    parser.add_argument(
        "--issue-field-org",
        help="GitHub organization that owns the official Issue Priority field.",
    )
    parser.add_argument(
        "--issue-priority-field",
        help="GitHub organization Issue single-select field name used for priority.",
    )
    parser.add_argument(
        "--issue-priority-field-id",
        help=(
            "Optional numeric GitHub Issue Priority field ID. Use this in automation "
            "when the token can edit issues but cannot list organization Issue fields."
        ),
    )
    args = parser.parse_args(argv)

    try:
        reports = discover_reports(args.directory)
    except BugReportValidationError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    if args.check:
        sys.stdout.write(f"Validated {len(reports)} bug report item(s).\n")
        return 0
    if not reports:
        sys.stdout.write(f"No bug report JSON files found in {args.directory}.\n")
        return 0

    assignee = args.assignee.strip() if args.assignee else None
    if not args.dry_run:
        if args.skip_planning_sync:
            sys.stderr.write(
                "--skip-planning-sync is not allowed for live bug sync because "
                "managed issues require the official GitHub Issue Priority field.\n"
            )
            return 1
        sync.check_gh_auth()

    try:
        issue_fields = (
            None
            if args.skip_planning_sync
            else github_issue_planning.issue_field_config_from_values(
                repo=args.repo,
                organization=args.issue_field_org,
                priority_field=args.issue_priority_field,
                priority_field_id=args.issue_priority_field_id,
            )
        )
    except github_issue_planning.PlanningSyncError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    if not args.skip_label_sync:
        labels = [label for report in reports for label in report.labels]
        sync.ensure_labels(args.repo, labels, dry_run=args.dry_run)

    for report in reports:
        sync_report(
            sync,
            args.repo,
            report,
            assignee=assignee,
            dry_run=args.dry_run,
            reopen_closed=args.reopen_closed,
            sync_planning=not args.skip_planning_sync,
            issue_fields=issue_fields,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
