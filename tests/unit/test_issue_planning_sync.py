# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for GitHub-native issue priority and relationship synchronization."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_issue_planning.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_issue_planning", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["github_issue_planning"] = module
    spec.loader.exec_module(module)
    return module


def test_sync_issue_planning_sets_native_issue_priority_field() -> None:
    script = _load_script()
    api_calls: list[tuple[list[str], dict[str, object] | None]] = []

    def fake_runner(args: list[str]) -> Any:
        raise AssertionError(f"unexpected call: {args}")

    def fake_api_runner(args: list[str], payload: dict[str, object] | None = None) -> Any:
        api_calls.append((args, payload))
        if args[0] == "orgs/ProjectCuillin/issue-fields":
            return [
                {
                    "id": 101,
                    "name": "Priority",
                    "data_type": "single_select",
                    "options": [
                        {"name": "Urgent"},
                        {"name": "High"},
                        {"name": "Medium"},
                        {"name": "Low"},
                    ],
                }
            ]
        if args[:2] == ["--method", "POST"]:
            return {}
        raise AssertionError(f"unexpected api call: {args}")

    script.sync_issue_planning(
        repo="ProjectCuillin/nats-sinks",
        issue=script.IssuePlanningSpec(
            number=42,
            url="https://github.invalid/ProjectCuillin/nats-sinks/issues/42",
            priority="P2 - next minor release candidate",
            relationships={},
        ),
        issue_fields=script.IssueFieldPlanningConfig(organization="ProjectCuillin"),
        dry_run=False,
        runner=fake_runner,
        api_runner=fake_api_runner,
    )

    assert (
        [
            "--method",
            "POST",
            "repos/ProjectCuillin/nats-sinks/issues/42/issue-field-values",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
            "--input",
            "-",
        ],
        {"issue_field_values": [{"field_id": 101, "value": "High"}]},
    ) in api_calls


def test_sync_issue_planning_applies_native_blocked_by_relationship() -> None:
    script = _load_script()
    calls: list[list[str]] = []

    def fake_runner(args: list[str]) -> Any:
        calls.append(args)
        if args[:2] == ["issue", "list"]:
            return [
                {
                    "number": 13,
                    "body": "<!-- nats-sinks-backlog-id: foundation-work -->",
                }
            ]
        if args[:1] == ["api"] and args[1].endswith("/issues/13"):
            return {"id": 1300}
        if args[:1] == ["api"] and args[1].endswith("/dependencies/blocked_by"):
            return []
        if args[:3] == ["api", "--method", "POST"]:
            return {}
        raise AssertionError(f"unexpected call: {args}")

    script.sync_issue_planning(
        repo="ProjectCuillin/nats-sinks",
        issue=script.IssuePlanningSpec(
            number=42,
            url="https://github.invalid/ProjectCuillin/nats-sinks/issues/42",
            priority="P3 - backlog candidate",
            relationships={"blocked_by": ("backlog:foundation-work",)},
        ),
        issue_fields=None,
        dry_run=False,
        runner=fake_runner,
    )

    assert [
        "api",
        "--method",
        "POST",
        "repos/ProjectCuillin/nats-sinks/issues/42/dependencies/blocked_by",
        "-H",
        "X-GitHub-Api-Version: 2026-03-10",
        "-f",
        "issue_id=1300",
    ] in calls


def test_issue_field_config_defaults_to_repo_owner(monkeypatch) -> None:
    script = _load_script()
    monkeypatch.delenv("NATS_SINKS_GITHUB_ISSUE_FIELD_ORG", raising=False)
    monkeypatch.delenv("NATS_SINKS_GITHUB_ISSUE_PRIORITY_FIELD", raising=False)

    config = script.issue_field_config_from_values(
        repo="ProjectCuillin/nats-sinks",
        organization=None,
        priority_field=None,
    )

    assert config.organization == "ProjectCuillin"
    assert config.priority_field == "Priority"


def test_issue_field_config_uses_explicit_overrides() -> None:
    script = _load_script()

    config = script.issue_field_config_from_values(
        repo="ProjectCuillin/nats-sinks",
        organization="Operations",
        priority_field="Mission Priority",
        priority_field_id="123",
    )

    assert config.organization == "Operations"
    assert config.priority_field == "Mission Priority"
    assert config.priority_field_id == 123


def test_issue_field_config_rejects_empty_priority_field() -> None:
    script = _load_script()

    try:
        script.issue_field_config_from_values(
            repo="ProjectCuillin/nats-sinks",
            organization="ProjectCuillin",
            priority_field=" ",
        )
    except script.PlanningSyncError as exc:
        assert "priority field name must not be empty" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("empty priority field name was accepted")


def test_issue_priority_field_requires_expected_options() -> None:
    script = _load_script()

    try:
        script._validate_issue_priority_options(
            {
                "id": 101,
                "name": "Priority",
                "data_type": "single_select",
                "options": [{"name": "High"}, {"name": "Medium"}, {"name": "Low"}],
            }
        )
    except script.PlanningSyncError as exc:
        assert "Urgent" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("priority field without Urgent option was accepted")


def test_sync_issue_planning_uses_explicit_field_id_without_listing_org_fields() -> None:
    script = _load_script()
    api_calls: list[tuple[list[str], dict[str, object] | None]] = []

    def fake_api_runner(args: list[str], payload: dict[str, object] | None = None) -> Any:
        api_calls.append((args, payload))
        if args[:2] == ["--method", "POST"]:
            return {}
        raise AssertionError(f"unexpected api call: {args}")

    script.sync_issue_planning(
        repo="ProjectCuillin/nats-sinks",
        issue=script.IssuePlanningSpec(
            number=42,
            url="https://github.invalid/ProjectCuillin/nats-sinks/issues/42",
            priority="P1 - release blocker",
            relationships={},
        ),
        issue_fields=script.IssueFieldPlanningConfig(
            organization="ProjectCuillin",
            priority_field_id=41029122,
        ),
        dry_run=False,
        api_runner=fake_api_runner,
    )

    assert api_calls == [
        (
            [
                "--method",
                "POST",
                "repos/ProjectCuillin/nats-sinks/issues/42/issue-field-values",
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                "X-GitHub-Api-Version: 2026-03-10",
                "--input",
                "-",
            ],
            {"issue_field_values": [{"field_id": 41029122, "value": "Urgent"}]},
        )
    ]
