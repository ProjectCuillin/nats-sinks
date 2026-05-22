# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Sync local backlog item JSON files to GitHub Issues.

GitHub Issues are the live backlog for this project. This script provides a
small, auditable bridge from locally defined backlog items to GitHub Issues
using the GitHub CLI (`gh`). It intentionally avoids the GitHub REST API
directly so maintainers can use the same authentication model they already use
for releases and workflow inspection.

The script is idempotent. Each generated issue body contains a hidden
`nats-sinks-backlog-id` marker. On later runs, the script searches for that
marker and updates the matching open issue instead of creating a duplicate.
Closed issues are left closed by default so shipped work is not accidentally
reopened just because an old local backlog file still exists.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import github_issue_planning  # noqa: E402 - scripts directory is added before import.

DEFAULT_REPO = "ProjectCuillin/nats-sinks"
DEFAULT_DIRECTORY = Path("backlog/items")
MARKER_PREFIX = "nats-sinks-backlog-id:"
CONTROL_CHARACTER_LIMIT = 32
ACCEPTANCE_ITEM_MAX_LENGTH = 500
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,100}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,49}$")
RELEASE_RE = re.compile(r"^(?:unscheduled|v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)$")
RELATIONSHIP_REF_RE = re.compile(r"^(?:#\d+|(?:backlog|bug):[a-z0-9][a-z0-9-]{2,100})$")
URL_RE = re.compile(r"(?i)\b(?:https?|ssh|ftp)://\S+|\bwww\.\S+")
IPV4_RE = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|private[_ -]?key|api[_ -]?key|credential)"
    r"\s*[:=]\s*\S+"
)
COMMON_TOKEN_RE = re.compile(
    r"(?i)\b(?:ghp|gho|github_pat|xox[baprs]-|sk-[A-Za-z0-9])[A-Za-z0-9_=-]{8,}"
)
PEM_BLOCK_RE = re.compile(r"-----BEGIN [A-Z0-9 ]+-----")

AREAS = {
    "Core runtime and delivery semantics",
    "Oracle sink",
    "File sink",
    "Future sink",
    "CLI",
    "Configuration",
    "Metrics and observability",
    "Security",
    "Packaging and release",
    "Documentation",
    "CI and automation",
    "Operations and deployment",
    "Testing",
    "Other",
}

PRIORITIES = {
    "P1 - release blocker",
    "P2 - next minor release candidate",
    "P3 - backlog candidate",
    "P4 - research or design needed",
}

LEGACY_PRIORITY_LABELS = frozenset(
    {
        "priority-p1",
        "priority-p2",
        "priority-p3",
        "priority-p4",
    }
)

RELATIONSHIP_KEYS = ("blocked_by", "blocks", "related")

DEFAULT_LABELS = ("enhancement", "backlog")

KNOWN_LABELS = {
    "backlog": ("5319e7", "Planned or proposed work that has not been implemented."),
    "enhancement": ("a2eeef", "A user-visible improvement or new capability."),
    "bug": ("d73a4a", "A reproducible defect."),
    "security": ("ee0701", "Security posture, redaction, authentication, or abuse-case work."),
    "documentation": ("0075ca", "Documentation-only or documentation-led work."),
    "sink-oracle": ("f9d0c4", "Oracle-specific work."),
    "sink-file": ("c5def5", "File sink work."),
    "sink-new": ("bfdadc", "Future sink work."),
    "sink-postgres": ("c2e0c6", "Postgres-specific sink work."),
    "sink-http": ("c2dfff", "HTTP-specific sink work."),
    "sink-s3": ("d4e8c0", "S3-specific sink work."),
    "sink-oci": ("f7d7bd", "Oracle Cloud Infrastructure Object Storage sink work."),
    "observability": ("d4c5f9", "Metrics, snapshots, Prometheus, or observability connector work."),
    "nats": ("0e8a16", "NATS connection, JetStream, consumer, ACK, advisory, or stream behavior."),
    "release": ("fbca04", "Packaging, PyPI, GitHub Releases, SBOM, CI, or release automation."),
    "deployment": (
        "bfd4f2",
        "Deployment, service, container, chart, or operations automation work.",
    ),
    "testing": (
        "cfd3d7",
        "Test-suite, certification, property-based, fuzz, load, or regression work.",
    ),
    "completed": (
        "0e8a16",
        "Implementation is complete in development and waiting for release-gated closure.",
    ),
    "release-unscheduled": ("ededed", "Work has not yet been assigned to a release tag."),
    "severity-critical": ("b60205", "Critical defect with severe reliability or security impact."),
    "severity-high": ("d93f0b", "High-impact defect affecting important behavior."),
    "severity-medium": ("fbca04", "Medium-impact defect with a contained workaround or scope."),
    "severity-low": ("c5def5", "Low-impact defect or minor correctness issue."),
}


@dataclass(frozen=True)
class BacklogItem:
    """Validated local representation of a GitHub backlog issue."""

    identifier: str
    title: str
    area: str
    priority: str
    target_release: str
    labels: tuple[str, ...]
    problem: str
    proposal: str
    users: str
    delivery_semantics: str
    security: str
    acceptance: tuple[str, ...]
    tests: str
    documentation: str
    closeout: str
    relationships: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class ExistingIssue:
    """Small subset of GitHub issue data needed for idempotent sync."""

    number: int
    state: str
    title: str
    url: str
    body: str
    labels: tuple[str, ...]


class BacklogValidationError(ValueError):
    """Raised when a local backlog JSON file is malformed or unsafe."""


def _reject_duplicate_json_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous local issue JSON before public text is generated."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject Python JSON extensions in local issue manifests."""

    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def load_local_json(path: Path) -> dict[str, object]:
    """Load one strict JSON object from a local backlog or bug manifest.

    Backlog and bug files are public issue source material.  They therefore use
    the same fail-closed JSON posture as runtime configuration: duplicate keys
    and Python-only constants are rejected before any field-level validation can
    ignore, coerce, or overwrite them.
    """

    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_object_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise BacklogValidationError(f"{path}: invalid JSON: {exc.msg}") from exc
    except ValueError as exc:
        raise BacklogValidationError(f"{path}: invalid JSON: {exc}") from exc
    return _as_mapping(raw, path=path)


def _release_label(target_release: str) -> str:
    """Return the GitHub label used to represent the planned release."""

    return f"release-{target_release}"


def _legacy_priority_labels_present(labels: Iterable[str]) -> tuple[str, ...]:
    """Return stale priority labels that should be removed from managed issues.

    Priority now belongs in the official GitHub Issue ``Priority`` field.
    Older managed issues may still carry ``priority-p*`` labels from the first
    automation iteration, so update paths remove those labels while preserving
    all non-priority labels used for search, sink area, release, and lifecycle.
    """

    return tuple(label for label in labels if label in LEGACY_PRIORITY_LABELS)


def validate_public_issue_text(text: str, *, path: Path | None = None, field: str = "text") -> None:
    """Reject sensitive patterns before public GitHub issue content is generated.

    Backlog files are intended to become public issue bodies. This validator is
    intentionally conservative: it blocks live network locators, IP literals,
    common token shapes, PEM blocks, and key-value assignments that look like
    credentials. The goal is not to prove that a text is non-sensitive; the goal
    is to catch the mistakes that most often leak from local notes into public
    project management systems.
    """

    prefix = f"{path}: " if path is not None else ""
    if URL_RE.search(text):
        raise BacklogValidationError(f"{prefix}{field!r} must not contain URLs.")
    if IPV4_RE.search(text):
        raise BacklogValidationError(f"{prefix}{field!r} must not contain IP addresses.")
    for token in re.findall(r"\b[0-9A-Fa-f:]{3,}\b", text):
        if ":" not in token:
            continue
        try:
            ipaddress.ip_address(token)
        except ValueError:
            continue
        raise BacklogValidationError(f"{prefix}{field!r} must not contain IP addresses.")
    if SECRET_ASSIGNMENT_RE.search(text):
        raise BacklogValidationError(f"{prefix}{field!r} must not contain credential assignments.")
    if COMMON_TOKEN_RE.search(text):
        raise BacklogValidationError(f"{prefix}{field!r} must not contain token-like values.")
    if PEM_BLOCK_RE.search(text):
        raise BacklogValidationError(
            f"{prefix}{field!r} must not contain certificate or key blocks."
        )


def _as_mapping(value: object, *, path: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BacklogValidationError(f"{path}: root JSON value must be an object.")
    return value


def _required_text(data: dict[str, object], key: str, *, path: Path, max_length: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BacklogValidationError(f"{path}: {key!r} must be a non-empty string.")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise BacklogValidationError(f"{path}: {key!r} is too long.")
    if any(ord(char) < CONTROL_CHARACTER_LIMIT and char not in "\n\t" for char in normalized):
        raise BacklogValidationError(f"{path}: {key!r} contains control characters.")
    validate_public_issue_text(normalized, path=path, field=key)
    return normalized


def _optional_labels(data: dict[str, object], *, path: Path) -> tuple[str, ...]:
    value = data.get("labels", [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BacklogValidationError(f"{path}: 'labels' must be a list of strings.")

    labels: list[str] = [*DEFAULT_LABELS]
    for label in value:
        normalized = label.strip()
        if not normalized:
            continue
        if not LABEL_RE.fullmatch(normalized):
            raise BacklogValidationError(f"{path}: invalid label {normalized!r}.")
        validate_public_issue_text(normalized, path=path, field="labels")
        labels.append(normalized)

    return tuple(dict.fromkeys(labels))


def _target_release(data: dict[str, object], *, path: Path) -> str:
    """Return the planned release tag or the explicit unscheduled marker."""

    value = data.get("target_release", "unscheduled")
    if not isinstance(value, str) or not value.strip():
        raise BacklogValidationError(f"{path}: 'target_release' must be a non-empty string.")
    normalized = value.strip()
    if not RELEASE_RE.fullmatch(normalized):
        raise BacklogValidationError(
            f"{path}: 'target_release' must be 'unscheduled' or a version tag like v1.2.3."
        )
    validate_public_issue_text(normalized, path=path, field="target_release")
    return normalized


def _acceptance(data: dict[str, object], *, path: Path) -> tuple[str, ...]:
    value = data.get("acceptance")
    if not isinstance(value, list) or not value:
        raise BacklogValidationError(f"{path}: 'acceptance' must be a non-empty list.")
    result: list[str] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise BacklogValidationError(f"{path}: acceptance item {index} must be text.")
        normalized = item.strip()
        if len(normalized) > ACCEPTANCE_ITEM_MAX_LENGTH:
            raise BacklogValidationError(f"{path}: acceptance item {index} is too long.")
        validate_public_issue_text(normalized, path=path, field=f"acceptance[{index}]")
        result.append(normalized)
    return tuple(result)


def _relationships(data: dict[str, object], *, path: Path) -> Mapping[str, tuple[str, ...]]:
    """Return validated issue relationships from an optional JSON object.

    GitHub native issue dependencies use concrete issue relationships such as
    "blocked by". The local backlog keeps those relationships in symbolic form
    so they can be written before GitHub issue numbers exist. References must be
    either ``#123`` for an existing issue, ``backlog:identifier`` for another
    managed feature request, or ``bug:identifier`` for a managed bug report.
    """

    value = data.get("relationships", {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BacklogValidationError(f"{path}: 'relationships' must be an object.")

    result: dict[str, tuple[str, ...]] = {}
    for key, refs in value.items():
        if key not in RELATIONSHIP_KEYS:
            allowed = ", ".join(RELATIONSHIP_KEYS)
            raise BacklogValidationError(
                f"{path}: unsupported relationship {key!r}; expected one of: {allowed}."
            )
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            raise BacklogValidationError(f"{path}: relationship {key!r} must be a list of refs.")
        normalized_refs: list[str] = []
        for ref in refs:
            normalized = ref.strip()
            if not RELATIONSHIP_REF_RE.fullmatch(normalized):
                raise BacklogValidationError(
                    f"{path}: invalid issue relationship reference {normalized!r}."
                )
            validate_public_issue_text(normalized, path=path, field=f"relationships.{key}")
            normalized_refs.append(normalized)
        if normalized_refs:
            result[str(key)] = tuple(dict.fromkeys(normalized_refs))
    return result


def load_backlog_item(path: Path) -> BacklogItem:
    """Load and validate one local backlog item JSON file."""

    data = load_local_json(path)

    identifier = _required_text(data, "id", path=path, max_length=100)
    if not ID_RE.fullmatch(identifier):
        raise BacklogValidationError(
            f"{path}: 'id' must use lowercase letters, numbers, and hyphens."
        )

    area = _required_text(data, "area", path=path, max_length=80)
    if area not in AREAS:
        raise BacklogValidationError(f"{path}: unsupported area {area!r}.")

    priority = _required_text(data, "priority", path=path, max_length=80)
    if priority not in PRIORITIES:
        raise BacklogValidationError(f"{path}: unsupported priority {priority!r}.")

    target_release = _target_release(data, path=path)
    labels = (
        *_optional_labels(data, path=path),
        _release_label(target_release),
    )

    return BacklogItem(
        identifier=identifier,
        title=_required_text(data, "title", path=path, max_length=180),
        area=area,
        priority=priority,
        target_release=target_release,
        labels=tuple(dict.fromkeys(labels)),
        problem=_required_text(data, "problem", path=path, max_length=4_000),
        proposal=_required_text(data, "proposal", path=path, max_length=4_000),
        users=_required_text(data, "users", path=path, max_length=2_000),
        delivery_semantics=_required_text(data, "delivery_semantics", path=path, max_length=3_000),
        security=_required_text(data, "security", path=path, max_length=3_000),
        acceptance=_acceptance(data, path=path),
        tests=_required_text(data, "tests", path=path, max_length=3_000),
        documentation=_required_text(data, "documentation", path=path, max_length=3_000),
        closeout=_required_text(data, "closeout", path=path, max_length=2_000),
        relationships=_relationships(data, path=path),
    )


def discover_items(directory: Path) -> list[BacklogItem]:
    """Return validated backlog items sorted by identifier."""

    if not directory.exists():
        return []
    paths = sorted(path for path in directory.glob("*.json") if path.is_file())
    items = [load_backlog_item(path) for path in paths]
    identifiers = [item.identifier for item in items]
    counts = Counter(identifiers)
    duplicates = sorted(identifier for identifier, count in counts.items() if count > 1)
    if duplicates:
        joined = ", ".join(duplicates)
        raise BacklogValidationError(f"Duplicate backlog identifiers: {joined}")
    return sorted(items, key=lambda item: item.identifier)


def render_issue_body(item: BacklogItem) -> str:
    """Render a detailed GitHub Issue body from a validated backlog item."""

    acceptance = "\n".join(f"- [ ] {entry}" for entry in item.acceptance)
    labels = ", ".join(f"`{label}`" for label in item.labels)
    relationships = _render_relationships(item.relationships)
    return f"""<!-- {MARKER_PREFIX} {item.identifier} -->

## Problem Statement

{item.problem}

## Proposed Outcome

{item.proposal}

## Intended Users And Operational Context

{item.users}

## Delivery Semantics And Idempotency Impact

{item.delivery_semantics}

## Security And Privacy Considerations

{item.security}

## Acceptance Criteria

{acceptance}

## Test Plan

{item.tests}

## Documentation And Release-Note Plan

{item.documentation}

## Close-Out Evidence Required

{item.closeout}

## Issue Relationships

{relationships}

## Triage Metadata

- Backlog ID: `{item.identifier}`
- Area: `{item.area}`
- Priority: `{item.priority}`
- Target release: `{item.target_release}`
- Labels: {labels}
"""


def _render_relationships(relationships: Mapping[str, tuple[str, ...]]) -> str:
    """Render public relationship metadata in a stable human-readable form."""

    if not relationships:
        return "No related issues declared in the local backlog item."
    lines: list[str] = []
    labels = {
        "blocked_by": "Blocked by",
        "blocks": "Blocks",
        "related": "Related",
    }
    for key in RELATIONSHIP_KEYS:
        refs = relationships.get(key)
        if refs:
            joined = ", ".join(f"`{ref}`" for ref in refs)
            lines.append(f"- {labels[key]}: {joined}")
    return "\n".join(lines)


def _run_gh(args: Sequence[str], *, capture_json: bool = False) -> object:
    gh_executable = shutil.which("gh")
    if gh_executable is None:
        raise FileNotFoundError("gh")
    completed = subprocess.run(  # noqa: S603 - fixed executable with argument list.
        [gh_executable, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if capture_json:
        return json.loads(completed.stdout or "[]")
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    return None


def check_gh_auth() -> None:
    """Fail early with a helpful message when GitHub CLI auth is unavailable."""

    try:
        _run_gh(["auth", "status", "--hostname", "github.com"])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            "GitHub CLI authentication is not available. Install gh and run "
            "`gh auth login --hostname github.com --web`, or run this script "
            "with --dry-run to validate local backlog files only."
        ) from exc


def existing_issue(repo: str, identifier: str) -> ExistingIssue | None:
    """Find an existing issue by the hidden backlog marker."""

    marker = f"{MARKER_PREFIX} {identifier}"
    search = f'"{MARKER_PREFIX} {identifier}" in:body repo:{repo}'
    raw = _run_gh(
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
            labels=_issue_label_names(issue),
        )
    return None


def _issue_label_names(issue: object) -> tuple[str, ...]:
    """Extract label names from a GitHub CLI issue payload."""

    if not isinstance(issue, dict):
        return ()
    raw_labels = issue.get("labels", [])
    if not isinstance(raw_labels, list):
        return ()
    names: list[str] = []
    for label in raw_labels:
        if isinstance(label, dict) and isinstance(label.get("name"), str):
            names.append(label["name"])
    return tuple(names)


def preserve_acceptance_checks(new_body: str, existing_body: str) -> str:
    """Preserve checked Acceptance Criteria items when refreshing an issue body.

    The local JSON files remain the source of truth for issue content, but live
    GitHub issues also carry lifecycle state in their checkboxes. Refreshing the
    body must not accidentally turn completed work back into unchecked work.
    """

    checked_items = _acceptance_item_text(existing_body, checked=True)
    if not checked_items:
        return new_body

    lines: list[str] = []
    in_acceptance = False
    for line in new_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_acceptance = stripped == "## Acceptance Criteria"
        if in_acceptance:
            left = line.lstrip()
            prefix = line[: len(line) - len(left)]
            if left.startswith("- [ ] ") and left[6:] in checked_items:
                lines.append(f"{prefix}- [x] {left[6:]}")
                continue
        lines.append(line)

    rendered = "\n".join(lines)
    if new_body.endswith("\n"):
        rendered += "\n"
    return rendered


def _acceptance_item_text(body: str, *, checked: bool) -> set[str]:
    wanted = "- [x] " if checked else "- [ ] "
    result: set[str] = set()
    in_acceptance = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_acceptance = stripped == "## Acceptance Criteria"
        if not in_acceptance:
            continue
        left = line.lstrip()
        if left.lower().startswith(wanted):
            result.add(left[6:])
    return result


def ensure_labels(repo: str, labels: Iterable[str], *, dry_run: bool) -> None:
    """Create known labels before issues are synced."""

    unique_labels = tuple(dict.fromkeys(labels))
    if dry_run:
        sys.stdout.write(f"would ensure labels: {', '.join(unique_labels)}\n")
        return

    raw = _run_gh(
        ["label", "list", "--repo", repo, "--limit", "1000", "--json", "name"],
        capture_json=True,
    )
    if not isinstance(raw, list):
        raise SystemExit("Unexpected GitHub CLI label list response.")
    existing = {str(label["name"]) for label in raw if isinstance(label, dict) and "name" in label}

    for label in unique_labels:
        if label in existing:
            continue
        color, description = KNOWN_LABELS.get(label, ("ededed", "Backlog sync label."))
        _run_gh(
            [
                "label",
                "create",
                label,
                "--repo",
                repo,
                "--color",
                color,
                "--description",
                description,
            ]
        )


def _remove_labels(repo: str, issue_number: int, labels: Iterable[str], *, dry_run: bool) -> None:
    """Remove labels from one issue using GitHub's normal issue-edit surface."""

    unique_labels = tuple(dict.fromkeys(labels))
    if not unique_labels:
        return
    if dry_run:
        sys.stdout.write(
            f"would remove labels from issue #{issue_number}: {', '.join(unique_labels)}\n"
        )
        return
    args = ["issue", "edit", str(issue_number), "--repo", repo]
    for label in unique_labels:
        args.extend(["--remove-label", label])
    _run_gh(args)


def _issue_args(repo: str, item: BacklogItem, body_path: Path) -> list[str]:
    args = ["--repo", repo, "--title", item.title, "--body-file", str(body_path)]
    for label in item.labels:
        args.extend(["--label", label])
    return args


def _sync_planning(
    repo: str,
    item: BacklogItem,
    issue: ExistingIssue,
    *,
    dry_run: bool,
    issue_fields: github_issue_planning.IssueFieldPlanningConfig | None,
) -> None:
    """Update required GitHub-native planning metadata for one issue."""

    if issue_fields is None and not item.relationships:
        return
    github_issue_planning.sync_issue_planning(
        repo=repo,
        issue=github_issue_planning.IssuePlanningSpec(
            number=issue.number,
            url=issue.url,
            priority=item.priority,
            relationships=item.relationships,
        ),
        issue_fields=issue_fields,
        dry_run=dry_run,
    )


def sync_item(  # noqa: PLR0912 - mirrors the create/update lifecycle explicitly.
    repo: str,
    item: BacklogItem,
    *,
    dry_run: bool,
    reopen_closed: bool,
    sync_planning: bool,
    issue_fields: github_issue_planning.IssueFieldPlanningConfig | None,
) -> None:
    """Create or update one GitHub issue."""

    body = render_issue_body(item)
    if dry_run:
        sys.stdout.write(f"would sync {item.identifier}: {item.title}\n")
        if sync_planning:
            if issue_fields is not None:
                sys.stdout.write(
                    "would sync GitHub Issue priority field for this issue after creation/update\n"
                )
            if item.relationships:
                sys.stdout.write("would sync declared GitHub issue relationships\n")
        return

    found = existing_issue(repo, item.identifier)
    if found and found.state.upper() == "CLOSED" and not reopen_closed:
        sys.stdout.write(f"skipping closed issue #{found.number}: {found.url}\n")
        return
    if found is not None:
        body = preserve_acceptance_checks(body, found.body)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file_obj:
        file_obj.write(body)
        body_path = Path(file_obj.name)

    try:
        if found is None:
            _run_gh(["issue", "create", *_issue_args(repo, item, body_path)])
            if sync_planning:
                created = existing_issue(repo, item.identifier)
                if created is not None:
                    _sync_planning(
                        repo,
                        item,
                        created,
                        dry_run=False,
                        issue_fields=issue_fields,
                    )
            return

        if found.state.upper() == "CLOSED" and reopen_closed:
            _run_gh(["issue", "reopen", str(found.number), "--repo", repo])

        edit_args = [
            "issue",
            "edit",
            str(found.number),
            "--repo",
            repo,
            "--title",
            item.title,
            "--body-file",
            str(body_path),
        ]
        for label in item.labels:
            edit_args.extend(["--add-label", label])
        _run_gh(edit_args)
        _remove_labels(
            repo,
            found.number,
            _legacy_priority_labels_present(found.labels),
            dry_run=False,
        )
        if sync_planning:
            _sync_planning(repo, item, found, dry_run=False, issue_fields=issue_fields)
    finally:
        body_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync local backlog item JSON files to GitHub Issues through gh."
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help="GitHub repository in owner/name form.",
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=DEFAULT_DIRECTORY,
        help="Directory containing local backlog JSON files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print intended actions.",
    )
    parser.add_argument("--check", action="store_true", help="Validate local backlog files only.")
    parser.add_argument(
        "--reopen-closed",
        action="store_true",
        help="Reopen closed issues if a matching local backlog file still exists.",
    )
    parser.add_argument(
        "--skip-label-sync",
        action="store_true",
        help="Do not create missing labels before syncing issues.",
    )
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
    args = parser.parse_args()

    try:
        items = discover_items(args.directory)
    except BacklogValidationError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    if not items:
        sys.stdout.write(f"No backlog JSON files found in {args.directory}.\n")
        return 0

    if args.check:
        sys.stdout.write(f"Validated {len(items)} backlog item(s).\n")
        return 0

    if not args.dry_run:
        if args.skip_planning_sync:
            sys.stderr.write(
                "--skip-planning-sync is not allowed for live backlog sync because "
                "managed issues require the official GitHub Issue Priority field.\n"
            )
            return 1
        check_gh_auth()

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
        labels = [label for item in items for label in item.labels]
        ensure_labels(args.repo, labels, dry_run=args.dry_run)

    for item in items:
        sync_item(
            args.repo,
            item,
            dry_run=args.dry_run,
            reopen_closed=args.reopen_closed,
            sync_planning=not args.skip_planning_sync,
            issue_fields=issue_fields,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
