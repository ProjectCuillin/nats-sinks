# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the Dependency Review GitHub Actions workflow.

The dependency-review job is part of the project's supply-chain control plane.
It should stay quiet and future-compatible so release evidence does not contain
avoidable runtime warnings.  This test intentionally inspects the workflow file
as text because the control under review is the pinned action reference itself.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "dependency-review.yml"
PR_GOVERNANCE_WORKFLOW = ROOT / ".github" / "workflows" / "pr-governance.yml"


def test_dependency_review_action_uses_node24_compatible_release() -> None:
    """Prevent regression to the Node.js 20 Dependency Review action line."""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "actions/dependency-review-action@v5" in workflow
    assert "actions/dependency-review-action@v4" not in workflow
    assert "ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION" not in workflow


def test_dependency_review_workflow_keeps_least_privilege_permissions() -> None:
    """Keep dependency review limited to read-only repository and PR access."""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "contents: read" in workflow
    assert "pull-requests: read" in workflow


def test_release_pr_gate_workflows_run_on_release_branch_updates() -> None:
    """Release PR checks must refresh when the release branch gets a new commit."""

    dependency_review = WORKFLOW.read_text(encoding="utf-8")
    governance = PR_GOVERNANCE_WORKFLOW.read_text(encoding="utf-8")

    assert "synchronize" in dependency_review
    assert "synchronize" in governance
