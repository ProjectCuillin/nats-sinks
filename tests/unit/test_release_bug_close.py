# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for release-gated managed bug close-out helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "close-released-bug-issues.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("close_released_bug_issues", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["close_released_bug_issues"] = module
    spec.loader.exec_module(module)
    return module


def test_has_bug_marker_requires_hidden_marker() -> None:
    script = _load_script()

    assert script.has_bug_marker(
        "<!-- nats-sinks-bug-id: sample-bug-report -->",
        "nats-sinks-bug-id:",
    )
    assert not script.has_bug_marker("A normal issue.", "nats-sinks-bug-id:")


def test_release_bug_close_ready_requires_tdd_evidence() -> None:
    script = _load_script()
    issue = {
        "body": """<!-- nats-sinks-bug-id: sample-bug-report -->

## Acceptance Criteria

- [x] Regression test is committed.
- [x] Fix is verified.
""",
        "comments": [
            {
                "body": """## Bug Work Note

## Regression Test Evidence

The focused regression test passes.

## Verification Evidence

The full relevant test path passes.

## Close-Out Evidence

Pending release.
"""
            }
        ],
    }

    ready, reason = script.release_close_ready(issue, "nats-sinks-bug-id:")

    assert ready is True
    assert reason == "ready"


def test_release_bug_close_ready_rejects_missing_evidence() -> None:
    script = _load_script()
    issue = {
        "body": """<!-- nats-sinks-bug-id: sample-bug-report -->

## Acceptance Criteria

- [x] Regression test is committed.
""",
        "comments": [{"body": "Fixed locally."}],
    }

    ready, reason = script.release_close_ready(issue, "nats-sinks-bug-id:")

    assert ready is False
    assert "evidence" in reason


def test_release_bug_close_ready_rejects_unchecked_acceptance() -> None:
    script = _load_script()
    issue = {
        "body": """<!-- nats-sinks-bug-id: sample-bug-report -->

## Acceptance Criteria

- [ ] Regression test is committed.
""",
        "comments": [
            {
                "body": """## Regression Test Evidence

Pass.

## Verification Evidence

Pass.

## Close-Out Evidence

Ready.
"""
            }
        ],
    }

    ready, reason = script.release_close_ready(issue, "nats-sinks-bug-id:")

    assert ready is False
    assert "acceptance criteria" in reason
