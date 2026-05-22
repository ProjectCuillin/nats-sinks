# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Synchronize managed issue priorities and relationships with GitHub.

This command is the migration and maintenance entry point for GitHub-native
planning metadata. It reads the same local backlog and bug report JSON files as
the regular sync scripts, finds the live managed GitHub issues through their
hidden markers, and then updates:

* the configured GitHub Issue single-select ``Priority`` field; and
* native GitHub issue dependencies for declared ``blocked_by`` and ``blocks``
  relationships.

Priority is maintained in GitHub's organization-level Issue ``Priority`` field,
not in a parallel priority-label taxonomy. Dry-runs and explicit
``--skip-priority-field`` runs remain available for local inspection or
relationship-only maintenance.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import github_issue_planning

ROOT = Path(__file__).resolve().parent
BACKLOG_SCRIPT = ROOT / "sync-backlog-issues.py"
BUG_SCRIPT = ROOT / "sync-bug-reports.py"


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Unable to load {path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _planning_specs(
    *,
    repo: str,
    backlog_directory: Path,
    bug_directory: Path,
) -> list[github_issue_planning.IssuePlanningSpec]:
    backlog = _load_script(BACKLOG_SCRIPT, "sync_backlog_issues")
    bugs = _load_script(BUG_SCRIPT, "sync_bug_reports")

    specs: list[github_issue_planning.IssuePlanningSpec] = []
    for item in backlog.discover_items(backlog_directory):
        issue = backlog.existing_issue(repo, item.identifier)
        if issue is None or issue.state.upper() == "CLOSED":
            continue
        specs.append(
            github_issue_planning.IssuePlanningSpec(
                number=issue.number,
                url=issue.url,
                priority=item.priority,
                relationships=item.relationships,
            )
        )

    sync = bugs._load_sync_script()
    for report in bugs.discover_reports(bug_directory):
        issue = bugs.existing_issue(sync, repo, report.identifier)
        if issue is None or issue.state.upper() == "CLOSED":
            continue
        specs.append(
            github_issue_planning.IssuePlanningSpec(
                number=issue.number,
                url=issue.url,
                priority=report.priority,
                relationships=report.relationships,
            )
        )
    return specs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync managed GitHub issue priority fields and relationships."
    )
    parser.add_argument("--repo", default="ProjectCuillin/nats-sinks", help="GitHub repository.")
    parser.add_argument(
        "--backlog-directory",
        type=Path,
        default=Path("backlog/items"),
        help="Directory containing local backlog JSON files.",
    )
    parser.add_argument(
        "--bug-directory",
        type=Path,
        default=Path("bugs/reports"),
        help="Directory containing local bug report JSON files.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print intended actions only.")
    parser.add_argument(
        "--skip-priority-field",
        action="store_true",
        help="Do not update GitHub Issue fields; use only for relationship-only maintenance.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate local issue metadata and GitHub auth assumptions only.",
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
        specs = _planning_specs(
            repo=args.repo,
            backlog_directory=args.backlog_directory,
            bug_directory=args.bug_directory,
        )
        issue_fields = (
            None
            if args.skip_priority_field
            else github_issue_planning.issue_field_config_from_values(
                repo=args.repo,
                organization=args.issue_field_org,
                priority_field=args.issue_priority_field,
                priority_field_id=args.issue_priority_field_id,
            )
        )
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    if args.check:
        configured = "configured" if issue_fields is not None else "not configured"
        sys.stdout.write(
            f"Validated {len(specs)} open managed issue(s); "
            f"GitHub Issue Priority field is {configured}.\n"
        )
        return 0

    if issue_fields is None:
        sys.stdout.write(
            "GitHub Issue priority sync skipped because --skip-priority-field was used.\n"
        )

    for spec in specs:
        try:
            github_issue_planning.sync_issue_planning(
                repo=args.repo,
                issue=spec,
                issue_fields=issue_fields,
                dry_run=args.dry_run,
            )
        except github_issue_planning.PlanningSyncError as exc:
            sys.stderr.write(f"issue #{spec.number}: {exc}\n")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
