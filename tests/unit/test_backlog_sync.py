# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for local backlog-to-GitHub issue sync helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync-backlog-issues.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_backlog_issues", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_backlog_issues"] = module
    spec.loader.exec_module(module)
    return module


def _valid_item() -> dict[str, object]:
    return {
        "id": "sample-backlog-item",
        "title": "[Feature]: Sample backlog item",
        "area": "Documentation",
        "priority": "P3 - backlog candidate",
        "target_release": "unscheduled",
        "labels": ["documentation"],
        "problem": "The project needs a sample issue for test coverage.",
        "proposal": "Render a detailed issue body from local JSON.",
        "users": "Maintainers who prepare detailed backlog items locally.",
        "delivery_semantics": "No delivery semantics change; commit-then-ACK remains unchanged.",
        "security": "No secrets or payloads are included in the local backlog item.",
        "acceptance": [
            "The issue body includes acceptance criteria.",
            "The issue body includes a hidden stable backlog identifier.",
        ],
        "tests": "pytest tests/unit/test_backlog_sync.py",
        "documentation": "Backlog management documentation explains the workflow.",
        "closeout": "Closing comments must include implementation, tests, docs, and limitations.",
    }


def test_load_backlog_item_and_render_issue_body(tmp_path: Path) -> None:
    script = _load_script()
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(_valid_item()), encoding="utf-8")

    item = script.load_backlog_item(item_path)
    body = script.render_issue_body(item)

    assert item.identifier == "sample-backlog-item"
    assert "documentation" in item.labels
    assert "release-unscheduled" in item.labels
    assert "priority-p3" not in item.labels
    assert "nats-sinks-backlog-id: sample-backlog-item" in body
    assert "Target release: `unscheduled`" in body
    assert "Priority: `P3 - backlog candidate`" in body
    assert "## Delivery Semantics And Idempotency Impact" in body
    assert "## Issue Relationships" in body
    assert "- [ ] The issue body includes acceptance criteria." in body


def test_discover_items_rejects_duplicate_identifiers(tmp_path: Path) -> None:
    script = _load_script()
    item = _valid_item()
    (tmp_path / "one.json").write_text(json.dumps(item), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps(item), encoding="utf-8")

    try:
        script.discover_items(tmp_path)
    except script.BacklogValidationError as exc:
        assert "Duplicate backlog identifiers" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("duplicate backlog identifiers were accepted")


def test_load_backlog_item_rejects_invalid_area(tmp_path: Path) -> None:
    script = _load_script()
    item = _valid_item()
    item["area"] = "Unknown"
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")

    try:
        script.load_backlog_item(item_path)
    except script.BacklogValidationError as exc:
        assert "unsupported area" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("invalid backlog area was accepted")


def test_load_backlog_item_rejects_urls_and_ip_addresses(tmp_path: Path) -> None:
    script = _load_script()
    item = _valid_item()
    item["problem"] = "This local note accidentally includes https://example.invalid"
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")

    try:
        script.load_backlog_item(item_path)
    except script.BacklogValidationError as exc:
        assert "must not contain URLs" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("URL-containing backlog item was accepted")

    item["problem"] = "This local note accidentally includes 192.0.2.10"
    item_path.write_text(json.dumps(item), encoding="utf-8")

    try:
        script.load_backlog_item(item_path)
    except script.BacklogValidationError as exc:
        assert "must not contain IP addresses" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("IP-containing backlog item was accepted")


def test_load_backlog_item_rejects_credential_assignments(tmp_path: Path) -> None:
    script = _load_script()
    item = _valid_item()
    item["security"] = "Bad local note with token=abc123SECRET"
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")

    try:
        script.load_backlog_item(item_path)
    except script.BacklogValidationError as exc:
        assert "must not contain credential assignments" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("credential assignment was accepted")


def test_load_backlog_item_accepts_concrete_release_tag(tmp_path: Path) -> None:
    script = _load_script()
    item = _valid_item()
    item["target_release"] = "v0.4.0"
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")

    loaded = script.load_backlog_item(item_path)

    assert loaded.target_release == "v0.4.0"
    assert "release-v0.4.0" in loaded.labels
    assert "priority-p3" not in loaded.labels


def test_load_backlog_item_keeps_priority_out_of_github_labels(tmp_path: Path) -> None:
    script = _load_script()
    priorities = {
        "P1 - release blocker",
        "P2 - next minor release candidate",
        "P3 - backlog candidate",
        "P4 - research or design needed",
    }

    for priority in priorities:
        item = _valid_item()
        item["priority"] = priority
        item_path = tmp_path / f"{priority[:2].casefold()}.json"
        item_path.write_text(json.dumps(item), encoding="utf-8")

        loaded = script.load_backlog_item(item_path)

        assert loaded.priority == priority
        assert all(not label.startswith("priority-") for label in loaded.labels)


def test_load_backlog_item_accepts_declared_issue_relationships(tmp_path: Path) -> None:
    script = _load_script()
    item = _valid_item()
    item["relationships"] = {
        "blocked_by": ["backlog:foundation-work", "bug:known-defect"],
        "blocks": ["#91"],
        "related": ["backlog:operator-docs"],
    }
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")

    loaded = script.load_backlog_item(item_path)
    body = script.render_issue_body(loaded)

    assert loaded.relationships["blocked_by"] == (
        "backlog:foundation-work",
        "bug:known-defect",
    )
    assert "- Blocked by: `backlog:foundation-work`, `bug:known-defect`" in body
    assert "- Blocks: `#91`" in body
    assert "- Related: `backlog:operator-docs`" in body


def test_load_backlog_item_rejects_invalid_issue_relationships(tmp_path: Path) -> None:
    script = _load_script()
    item = _valid_item()
    item["relationships"] = {"blocked_by": ["https://example.invalid/issue/1"]}
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")

    try:
        script.load_backlog_item(item_path)
    except script.BacklogValidationError as exc:
        assert "invalid issue relationship reference" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("invalid relationship reference was accepted")


def test_existing_issue_requires_exact_hidden_marker(monkeypatch) -> None:
    script = _load_script()

    def fake_run_gh(args, *, capture_json=False):
        assert capture_json is True
        assert "issue" in args
        return [
            {
                "number": 16,
                "state": "OPEN",
                "title": "[Docs]: Add Kubernetes deployment examples",
                "url": "redacted",
                "body": "<!-- nats-sinks-backlog-id: kubernetes-examples -->",
            },
            {
                "number": 46,
                "state": "OPEN",
                "title": "[Feature]: Add a Helm chart for Kubernetes deployments",
                "url": "redacted",
                "body": "<!-- nats-sinks-backlog-id: helm-chart -->",
                "labels": [{"name": "priority-p2"}, {"name": "backlog"}],
            },
        ]

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    issue = script.existing_issue("ProjectCuillin/nats-sinks", "helm-chart")

    assert issue is not None
    assert issue.number == 46
    assert issue.labels == ("priority-p2", "backlog")


def test_legacy_priority_labels_are_detected_for_cleanup() -> None:
    script = _load_script()

    labels = script._legacy_priority_labels_present(
        ["backlog", "priority-p1", "release-v0.4.0", "priority-p3"]
    )

    assert labels == ("priority-p1", "priority-p3")


def test_remove_labels_uses_issue_edit_remove_label(monkeypatch) -> None:
    script = _load_script()
    calls = []

    def fake_run_gh(args, *, capture_json=False):
        assert capture_json is False
        calls.append(args)

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    script._remove_labels(
        "ProjectCuillin/nats-sinks", 42, ["priority-p1", "priority-p1"], dry_run=False
    )

    assert calls == [
        [
            "issue",
            "edit",
            "42",
            "--repo",
            "ProjectCuillin/nats-sinks",
            "--remove-label",
            "priority-p1",
        ]
    ]


def test_preserve_acceptance_checks_keeps_completed_items() -> None:
    script = _load_script()
    existing = """## Acceptance Criteria

- [x] Completed item.
- [ ] Open item.

## Test Plan
"""
    refreshed = """## Acceptance Criteria

- [ ] Completed item.
- [ ] Open item.

## Test Plan
"""

    rendered = script.preserve_acceptance_checks(refreshed, existing)

    assert "- [x] Completed item." in rendered
    assert "- [ ] Open item." in rendered
