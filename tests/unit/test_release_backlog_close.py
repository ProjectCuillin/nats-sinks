# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for release-gated backlog issue close-out helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "close-released-backlog-issues.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("close_released_backlog_issues", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["close_released_backlog_issues"] = module
    spec.loader.exec_module(module)
    return module


def test_has_backlog_marker_requires_hidden_marker() -> None:
    script = _load_script()

    assert script.has_backlog_marker(
        "<!-- nats-sinks-backlog-id: hash-verified-installation-guidance -->",
        "nats-sinks-backlog-id:",
    )
    assert not script.has_backlog_marker(
        "A normal issue that happens to mention backlog work.",
        "nats-sinks-backlog-id:",
    )


def test_release_close_comment_is_sanitized() -> None:
    script = _load_script()
    comment = script.render_close_comment("v0.4.0")

    assert "Released in `v0.4.0`." in comment
    assert "GitHub Release" in comment
    assert "http" not in comment.lower()


def test_release_close_ready_requires_checked_criteria_and_closeout() -> None:
    script = _load_script()
    issue = {
        "body": """<!-- nats-sinks-backlog-id: sample -->

## Acceptance Criteria

- [x] Tests passed.
- [x] Documentation updated.
""",
        "comments": [
            {
                "body": """## Backlog Work Note

## Completed Work

Done.

## Acceptance Criteria

All checked.

## Test Plan Evidence

Focused and full checks passed.

## Close-Out Evidence

Pending release.
"""
            }
        ],
    }

    ready, reason = script.release_close_ready(issue, "nats-sinks-backlog-id:")

    assert ready is True
    assert reason == "ready"


def test_release_close_ready_rejects_unchecked_criteria() -> None:
    script = _load_script()
    issue = {
        "body": """<!-- nats-sinks-backlog-id: sample -->

## Acceptance Criteria

- [ ] Tests passed.
""",
        "comments": [
            {
                "body": """## Test Plan Evidence

Checks passed.

## Close-Out Evidence

Pending release.
"""
            }
        ],
    }

    ready, reason = script.release_close_ready(issue, "nats-sinks-backlog-id:")

    assert ready is False
    assert "acceptance criteria" in reason


def test_release_close_ready_rejects_missing_closeout_evidence() -> None:
    script = _load_script()
    issue = {
        "body": """<!-- nats-sinks-backlog-id: sample -->

## Acceptance Criteria

- [x] Tests passed.
""",
        "comments": [{"body": "Implementation completed."}],
    }

    ready, reason = script.release_close_ready(issue, "nats-sinks-backlog-id:")

    assert ready is False
    assert "close-out evidence" in reason


def test_close_script_dry_run_lists_only_managed_issues(monkeypatch, capsys) -> None:
    script = _load_script()

    def fake_list_released_backlog_issues(sync, *, repo, release, limit):
        return [
            {
                "number": 14,
                "title": "[Docs]: Add hash-verified installation guidance",
                "body": """<!-- nats-sinks-backlog-id: hash-verified-installation-guidance -->

## Acceptance Criteria

- [x] Checksum manifests are generated.
""",
                "comments": [
                    {
                        "body": """## Backlog Work Note

## Test Plan Evidence

Focused and full checks passed.

## Close-Out Evidence

Ready for release-gated close.
"""
                    }
                ],
            },
            {
                "number": 99,
                "title": "Unmanaged issue",
                "body": "No managed marker.",
                "comments": [],
            },
        ]

    monkeypatch.setattr(script, "list_released_backlog_issues", fake_list_released_backlog_issues)

    result = script.main(["--release", "v0.4.0", "--dry-run"])

    assert result == 0
    output = capsys.readouterr().out
    assert "would close issue #14" in output
    assert "would close issue #99" not in output
