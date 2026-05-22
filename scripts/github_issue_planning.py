# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Synchronize managed issue planning metadata with GitHub-native surfaces.

The repository keeps bug reports and backlog items as sanitized local JSON
documents. GitHub organization issue fields provide the official typed planning
metadata that maintainers use for sorting, filtering, and release triage. This
helper bridges local JSON and GitHub-native issue metadata:

* it writes the local priority into the organization-level Issue field named
  ``Priority`` by default; and
* it applies GitHub's native issue dependency relationships for ``blocked_by``
  and ``blocks`` references when the local item declares them.

The public GitHub issue-field preview ships a default ``Priority`` field with
``Urgent``, ``High``, ``Medium``, and ``Low`` values. The local project keeps a
more explicit release-planning vocabulary, and this module maps it to those
native GitHub values in one place.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

DEFAULT_PRIORITY_FIELD = "Priority"
ISSUE_API_VERSION = "2026-03-10"
REPO_PART_COUNT = 2
ISSUE_FIELD_API_ACCEPT = "Accept: application/vnd.github+json"

ISSUE_FIELD_PRIORITY_VALUES = {
    "P1 - release blocker": "Urgent",
    "P2 - next minor release candidate": "High",
    "P3 - backlog candidate": "Medium",
    "P4 - research or design needed": "Low",
}

Runner = Callable[[Sequence[str]], object]
ApiRunner = Callable[[Sequence[str], Mapping[str, object] | None], object]
_ISSUE_FIELD_CACHE: dict[tuple[str, str], Mapping[str, object]] = {}


@dataclass(frozen=True)
class IssuePlanningSpec:
    """Planning metadata for one already-created GitHub issue."""

    number: int
    url: str
    priority: str
    relationships: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class IssueFieldPlanningConfig:
    """GitHub organization Issue field settings used for managed priorities."""

    organization: str
    priority_field: str = DEFAULT_PRIORITY_FIELD
    priority_field_id: int | None = None


class PlanningSyncError(RuntimeError):
    """Raised when GitHub-native planning metadata cannot be synchronized."""


def run_gh(args: Sequence[str]) -> object:
    """Run ``gh`` with a fixed executable and argument list."""

    gh_executable = shutil.which("gh")
    if gh_executable is None:
        raise PlanningSyncError("GitHub CLI is not installed.")
    completed = subprocess.run(  # noqa: S603 - fixed executable with argument list.
        [gh_executable, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.stdout.strip():
        return json.loads(completed.stdout)
    return None


def run_gh_api(args: Sequence[str], payload: Mapping[str, object] | None = None) -> object:
    """Run ``gh api`` with optional JSON input.

    GitHub's Issue Field Values endpoint accepts structured JSON bodies. The
    helper keeps command construction fixed and passes the body through stdin so
    no value is interpolated into a shell command or exposed as process-list
    arguments.
    """

    gh_executable = shutil.which("gh")
    if gh_executable is None:
        raise PlanningSyncError("GitHub CLI is not installed.")
    completed = subprocess.run(  # noqa: S603 - fixed executable with argument list.
        [gh_executable, "api", *args],
        check=True,
        capture_output=True,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        timeout=60,
    )
    if completed.stdout.strip():
        return json.loads(completed.stdout)
    return None


def issue_field_config_from_values(
    *,
    repo: str,
    organization: str | None,
    priority_field: str | None,
    priority_field_id: int | str | None = None,
) -> IssueFieldPlanningConfig:
    """Return GitHub Issue field configuration from CLI values and repo owner."""

    repo_owner, _repo_name = _split_repo(repo)
    organization_text = (
        organization or os.environ.get("NATS_SINKS_GITHUB_ISSUE_FIELD_ORG") or repo_owner
    ).strip()
    field = (
        priority_field
        or os.environ.get("NATS_SINKS_GITHUB_ISSUE_PRIORITY_FIELD")
        or DEFAULT_PRIORITY_FIELD
    ).strip()
    if not organization_text:
        raise PlanningSyncError("GitHub Issue field organization must not be empty.")
    if not field:
        raise PlanningSyncError("GitHub Issue priority field name must not be empty.")
    return IssueFieldPlanningConfig(
        organization=organization_text,
        priority_field=field,
        priority_field_id=_configured_issue_field_id(priority_field_id),
    )


def _configured_issue_field_id(value: int | str | None) -> int | None:
    """Return an optional explicit Issue field ID from CLI or environment."""

    raw = (
        value if value is not None else os.environ.get("NATS_SINKS_GITHUB_ISSUE_PRIORITY_FIELD_ID")
    )
    if raw is None or str(raw).strip() == "":
        return None
    try:
        field_id = int(str(raw).strip())
    except ValueError as exc:
        raise PlanningSyncError(
            "GitHub Issue priority field ID must be an integer when provided."
        ) from exc
    if field_id <= 0:
        raise PlanningSyncError(
            "GitHub Issue priority field ID must be greater than zero when provided."
        )
    return field_id


def sync_issue_planning(
    *,
    repo: str,
    issue: IssuePlanningSpec,
    issue_fields: IssueFieldPlanningConfig | None,
    dry_run: bool,
    runner: Runner = run_gh,
    api_runner: ApiRunner = run_gh_api,
) -> None:
    """Synchronize native issue priority and dependency relationships."""

    if issue_fields is not None:
        _sync_issue_field_priority(
            repo=repo,
            issue=issue,
            config=issue_fields,
            dry_run=dry_run,
            api_runner=api_runner,
        )
    _sync_issue_relationships(repo=repo, issue=issue, dry_run=dry_run, runner=runner)


def _sync_issue_field_priority(
    *,
    repo: str,
    issue: IssuePlanningSpec,
    config: IssueFieldPlanningConfig,
    dry_run: bool,
    api_runner: ApiRunner,
) -> None:
    """Set the organization-level GitHub Issue ``Priority`` field."""

    value = _issue_priority_value(issue.priority)
    if dry_run:
        sys.stdout.write(
            f"would set GitHub Issue field {config.organization}/{config.priority_field} "
            f"for issue #{issue.number} to {value}\n"
        )
        return
    if config.priority_field_id is not None:
        _put_issue_field_value(
            repo=repo,
            issue_number=issue.number,
            field_id=config.priority_field_id,
            value=value,
            api_runner=api_runner,
        )
        return
    field = _issue_priority_field(config=config, api_runner=api_runner)
    _put_issue_field_value(
        repo=repo,
        issue_number=issue.number,
        field_id=_required_int(field, "id"),
        value=value,
        api_runner=api_runner,
    )


def _issue_priority_value(priority: str) -> str:
    try:
        return ISSUE_FIELD_PRIORITY_VALUES[priority]
    except KeyError as exc:
        raise PlanningSyncError(f"Unsupported issue priority {priority!r}.") from exc


def _issue_priority_field(
    *, config: IssueFieldPlanningConfig, api_runner: ApiRunner
) -> Mapping[str, object]:
    """Return a cached organization-level issue priority field definition."""

    cache_key = (config.organization, config.priority_field.casefold())
    cached = _ISSUE_FIELD_CACHE.get(cache_key)
    if cached is not None:
        return cached
    raw = _expect_sequence(
        api_runner(
            [
                f"orgs/{config.organization}/issue-fields",
                "-H",
                ISSUE_FIELD_API_ACCEPT,
                "-H",
                f"X-GitHub-Api-Version: {ISSUE_API_VERSION}",
            ],
            None,
        )
    )
    field = _find_issue_priority_field(raw, config.priority_field)
    _validate_issue_priority_options(field)
    _ISSUE_FIELD_CACHE[cache_key] = field
    return field


def _find_issue_priority_field(fields: Sequence[object], field_name: str) -> Mapping[str, object]:
    for field in fields:
        if not isinstance(field, dict):
            continue
        if str(field.get("name", "")).casefold() == field_name.casefold():
            if str(field.get("data_type", "")).casefold() != "single_select":
                raise PlanningSyncError(f"GitHub Issue field {field_name!r} must be single_select.")
            return field
    raise PlanningSyncError(f"GitHub Issue field {field_name!r} was not found.")


def _validate_issue_priority_options(field: Mapping[str, object]) -> None:
    raw_options = field.get("options", [])
    if not isinstance(raw_options, list):
        raise PlanningSyncError("GitHub Issue priority field does not expose options.")
    available = {
        str(option.get("name", "")).casefold() for option in raw_options if isinstance(option, dict)
    }
    missing = sorted(
        value for value in ISSUE_FIELD_PRIORITY_VALUES.values() if value.casefold() not in available
    )
    if missing:
        joined = ", ".join(missing)
        raise PlanningSyncError(
            f"GitHub Issue priority field is missing required option(s): {joined}."
        )


def _put_issue_field_value(
    *,
    repo: str,
    issue_number: int,
    field_id: int,
    value: str,
    api_runner: ApiRunner,
) -> None:
    owner, repo_name = _split_repo(repo)
    api_runner(
        [
            "--method",
            "POST",
            f"repos/{owner}/{repo_name}/issues/{issue_number}/issue-field-values",
            "-H",
            ISSUE_FIELD_API_ACCEPT,
            "-H",
            f"X-GitHub-Api-Version: {ISSUE_API_VERSION}",
            "--input",
            "-",
        ],
        {"issue_field_values": [{"field_id": field_id, "value": value}]},
    )


def _sync_issue_relationships(
    *, repo: str, issue: IssuePlanningSpec, dry_run: bool, runner: Runner
) -> None:
    owner, repo_name = _split_repo(repo)
    for ref in issue.relationships.get("blocked_by", ()):
        target_number = _resolve_issue_number(repo=repo, ref=ref, runner=runner)
        _add_blocked_by(
            owner=owner,
            repo=repo_name,
            issue_number=issue.number,
            blocking_issue_number=target_number,
            dry_run=dry_run,
            runner=runner,
        )
    for ref in issue.relationships.get("blocks", ()):
        target_number = _resolve_issue_number(repo=repo, ref=ref, runner=runner)
        _add_blocked_by(
            owner=owner,
            repo=repo_name,
            issue_number=target_number,
            blocking_issue_number=issue.number,
            dry_run=dry_run,
            runner=runner,
        )


def _add_blocked_by(
    *,
    owner: str,
    repo: str,
    issue_number: int,
    blocking_issue_number: int,
    dry_run: bool,
    runner: Runner,
) -> None:
    if issue_number == blocking_issue_number:
        raise PlanningSyncError("An issue cannot depend on itself.")
    if dry_run:
        sys.stdout.write(
            f"would mark issue #{issue_number} as blocked by #{blocking_issue_number}\n"
        )
        return

    dependency = _issue_database_id(
        owner=owner, repo=repo, issue_number=blocking_issue_number, runner=runner
    )
    existing = _expect_sequence(
        runner(
            [
                "api",
                f"repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by",
                "-H",
                f"X-GitHub-Api-Version: {ISSUE_API_VERSION}",
            ]
        )
    )
    if any(isinstance(item, dict) and item.get("id") == dependency for item in existing):
        return
    runner(
        [
            "api",
            "--method",
            "POST",
            f"repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by",
            "-H",
            f"X-GitHub-Api-Version: {ISSUE_API_VERSION}",
            "-f",
            f"issue_id={dependency}",
        ]
    )


def _issue_database_id(*, owner: str, repo: str, issue_number: int, runner: Runner) -> int:
    raw = _expect_mapping(runner(["api", f"repos/{owner}/{repo}/issues/{issue_number}"]))
    value = raw.get("id")
    if not isinstance(value, int):
        raise PlanningSyncError(f"GitHub issue #{issue_number} did not expose a numeric id.")
    return value


def _resolve_issue_number(*, repo: str, ref: str, runner: Runner) -> int:
    if ref.startswith("#"):
        return int(ref[1:])
    prefix, identifier = ref.split(":", 1)
    marker = "nats-sinks-backlog-id:" if prefix == "backlog" else "nats-sinks-bug-id:"
    search = f'"{marker} {identifier}" in:body repo:{repo}'
    raw = _expect_sequence(
        runner(
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
                "number,body",
                "--limit",
                "5",
            ]
        )
    )
    needle = f"{marker} {identifier}"
    for issue in raw:
        if isinstance(issue, dict) and needle in str(issue.get("body", "")):
            number = issue.get("number")
            if isinstance(number, int):
                return number
    raise PlanningSyncError(f"Could not resolve issue relationship reference {ref!r}.")


def _split_repo(repo: str) -> tuple[str, str]:
    parts = repo.split("/", 1)
    if len(parts) != REPO_PART_COUNT or not all(parts):
        raise PlanningSyncError("Repository must be in owner/name form.")
    return parts[0], parts[1]


def _expect_mapping(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise PlanningSyncError("Unexpected GitHub CLI JSON response.")
    return raw


def _expect_sequence(raw: object) -> Sequence[object]:
    if not isinstance(raw, list):
        raise PlanningSyncError("Unexpected GitHub CLI JSON response.")
    return raw


def _required_text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise PlanningSyncError(f"GitHub response did not include text field {key!r}.")
    return value


def _required_int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise PlanningSyncError(f"GitHub response did not include integer field {key!r}.")
    return value
