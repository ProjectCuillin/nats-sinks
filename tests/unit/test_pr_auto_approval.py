# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for guarded non-main pull request auto-approval."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "approve-non-main-pr.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("approve_non_main_pr", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["approve_non_main_pr"] = module
    spec.loader.exec_module(module)
    return module


def _pr(script: ModuleType, *, base: str = "release-v0.4.1", draft: bool = False) -> Any:
    return script.PullRequestSummary(
        number=123,
        base_ref_name=base,
        head_ref_name="issue-123-example",
        state="OPEN",
        is_draft=draft,
        author_login="louwersj",
        url="https://github.com/ProjectCuillin/nats-sinks/pull/123",
    )


def test_auto_approval_refuses_pull_requests_targeting_main() -> None:
    script = _load_script()

    with pytest.raises(script.ApprovalError, match="targeting main"):
        script.validate_pull_request_for_auto_approval(_pr(script, base="main"))


def test_auto_approval_refuses_closed_pull_requests() -> None:
    script = _load_script()
    pr = _pr(script)
    closed = script.PullRequestSummary(
        number=pr.number,
        base_ref_name=pr.base_ref_name,
        head_ref_name=pr.head_ref_name,
        state="CLOSED",
        is_draft=pr.is_draft,
        author_login=pr.author_login,
        url=pr.url,
    )

    with pytest.raises(script.ApprovalError, match="CLOSED"):
        script.validate_pull_request_for_auto_approval(closed)


def test_auto_approval_refuses_draft_pull_requests_by_default() -> None:
    script = _load_script()

    with pytest.raises(script.ApprovalError, match="draft"):
        script.validate_pull_request_for_auto_approval(_pr(script, draft=True))


def test_auto_approval_can_allow_draft_pull_requests_explicitly() -> None:
    script = _load_script()

    script.validate_pull_request_for_auto_approval(_pr(script, draft=True), allow_draft=True)


def test_auto_approval_refuses_unexpected_author() -> None:
    script = _load_script()

    with pytest.raises(script.ApprovalError, match="expected author"):
        script.validate_pull_request_for_auto_approval(
            _pr(script),
            expected_author="other-maintainer",
        )


def test_main_dry_run_validates_without_reviewing(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    script = _load_script()
    calls: list[list[str]] = []

    def fake_run_gh(args: list[str], **kwargs: object) -> dict[str, object] | str:
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return {
                "number": 123,
                "baseRefName": "release-v0.4.1",
                "headRefName": "issue-123-example",
                "state": "OPEN",
                "isDraft": False,
                "author": {"login": "louwersj"},
                "url": "https://github.com/ProjectCuillin/nats-sinks/pull/123",
            }
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    result = script.main(
        [
            "--repo",
            "ProjectCuillin/nats-sinks",
            "--pr",
            "123",
            "--expected-author",
            "louwersj",
            "--dry-run",
        ]
    )

    assert result == 0
    assert calls == [
        [
            "pr",
            "view",
            "123",
            "--repo",
            "ProjectCuillin/nats-sinks",
            "--json",
            "number,baseRefName,headRefName,state,isDraft,author,url",
        ]
    ]
    assert "eligible for non-main auto-approval" in capsys.readouterr().out


def test_main_approves_ready_non_main_pull_request(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    script = _load_script()
    calls: list[list[str]] = []

    def fake_run_gh(args: list[str], **kwargs: object) -> dict[str, object] | str:
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return {
                "number": 123,
                "baseRefName": "release-v0.4.1",
                "headRefName": "issue-123-example",
                "state": "OPEN",
                "isDraft": False,
                "author": {"login": "louwersj"},
                "url": "https://github.com/ProjectCuillin/nats-sinks/pull/123",
            }
        if args[:3] == ["pr", "review", "123"]:
            return ""
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(script, "_run_gh", fake_run_gh)

    result = script.main(
        [
            "--repo",
            "ProjectCuillin/nats-sinks",
            "--pr",
            "123",
            "--expected-author",
            "louwersj",
        ]
    )

    assert result == 0
    assert calls[1] == [
        "pr",
        "review",
        "123",
        "--repo",
        "ProjectCuillin/nats-sinks",
        "--approve",
        "--body",
        (
            "Automated non-main issue-branch approval. This approval is only "
            "valid for a PR whose base branch is not main; release PRs into "
            "main remain manual."
        ),
    ]
    assert "Approved PR #123" in capsys.readouterr().out


def test_open_release_pr_auto_approves_ready_non_main_pr(tmp_path: Path) -> None:
    """Run the shell helper with fake GitHub and Git commands.

    This regression test proves that ready non-main pull requests use the
    approval helper without touching the network. It also protects the Bash
    ``set -u`` path where an empty draft-argument list previously caused the
    helper to stop before creating the PR.
    """

    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir()
    state_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_gh = bin_dir / "gh"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "branch" && "$2" == "--show-current" ]]; then
  echo "issue-123-example"
  exit 0
fi
if [[ "$1" == "push" ]]; then
  exit 0
fi
echo "unexpected git command: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_gh.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
STATE_DIR={state_dir}
printf '%s\n' "$*" >> "$STATE_DIR/gh-calls"
if [[ "$1 $2" == "pr list" ]]; then
  if [[ -f "$STATE_DIR/created" ]]; then
    printf '321\\n'
  else
    printf ''
  fi
  exit 0
fi
if [[ "$1 $2" == "pr create" ]]; then
  touch "$STATE_DIR/created"
  printf 'https://github.com/ProjectCuillin/nats-sinks/pull/321\\n'
  exit 0
fi
if [[ "$1 $2" == "api user" ]]; then
  printf 'louwersj\\n'
  exit 0
fi
if [[ "$1 $2" == "pr view" ]]; then
  cat <<'JSON'
{{
  "number": 321,
  "baseRefName": "release-v0.4.1",
  "headRefName": "issue-123-example",
  "state": "OPEN",
  "isDraft": false,
  "author": {{"login": "louwersj"}},
  "url": "https://github.com/ProjectCuillin/nats-sinks/pull/321",
  "body": "Use `Related #123` as an example."
}}
JSON
  exit 0
fi
if [[ "$1 $2" == "pr review" ]]; then
  touch "$STATE_DIR/reviewed"
  exit 0
fi
if [[ "$1 $2 $3" == "issue view 123" ]]; then
  cat <<'JSON'
{{"labels":[{{"name":"enhancement"}},{{"name":"release-v0.4.1"}}]}}
JSON
  exit 0
fi
if [[ "$1 $2" == "issue edit" ]]; then
  touch "$STATE_DIR/labeled"
  exit 0
fi
echo "unexpected gh command: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_gh.chmod(0o755)
    bash_path = shutil.which("bash")
    if bash_path is None:  # pragma: no cover - platform guard
        pytest.skip("bash is required for the shell helper regression test")

    result = subprocess.run(  # noqa: S603 - fixed shell executable and local helper path.
        [
            bash_path,
            str(ROOT / "scripts" / "open-release-pr.sh"),
            "--repo",
            "ProjectCuillin/nats-sinks",
            "--base",
            "release-v0.4.1",
            "--ready",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "NATS_SINKS_COPY_ISSUE_LABELS_TO_PR": "true",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert (state_dir / "created").exists()
    assert (state_dir / "reviewed").exists()
    assert (state_dir / "labeled").exists(), (state_dir / "gh-calls").read_text(encoding="utf-8")


def test_open_release_pr_refreshes_existing_pr_with_issue_edit_and_related_issues(
    tmp_path: Path,
) -> None:
    """Existing PR refresh should avoid ``gh pr edit`` and render issue links."""

    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir()
    state_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_gh = bin_dir / "gh"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "branch" && "$2" == "--show-current" ]]; then
  echo "bug-213-example"
  exit 0
fi
if [[ "$1" == "push" ]]; then
  exit 0
fi
echo "unexpected git command: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_gh.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
STATE_DIR={state_dir}
printf '%s\n' "$*" >> "$STATE_DIR/gh-calls"
if [[ "$1 $2" == "pr list" ]]; then
  printf '321\\n'
  exit 0
fi
if [[ "$1 $2" == "issue edit" ]]; then
  body_file=""
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--body-file" ]]; then
      body_file="$2"
      break
    fi
    shift
  done
  cp "$body_file" "$STATE_DIR/body"
  touch "$STATE_DIR/refreshed"
  exit 0
fi
echo "unexpected gh command: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_gh.chmod(0o755)
    bash_path = shutil.which("bash")
    if bash_path is None:  # pragma: no cover - platform guard
        pytest.skip("bash is required for the shell helper regression test")

    result = subprocess.run(  # noqa: S603 - fixed shell executable and local helper path.
        [
            bash_path,
            str(ROOT / "scripts" / "open-release-pr.sh"),
            "--repo",
            "ProjectCuillin/nats-sinks",
            "--base",
            "release-v0.4.1",
            "--issue",
            "213",
            "--issue",
            "214",
            "--no-copy-issue-labels-to-pr",
        ],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert (state_dir / "refreshed").exists()
    calls = (state_dir / "gh-calls").read_text(encoding="utf-8")
    assert "issue edit 321" in calls
    assert "pr edit" not in calls
    body = (state_dir / "body").read_text(encoding="utf-8")
    assert "- Related #213" in body
    assert "- Related #214" in body
