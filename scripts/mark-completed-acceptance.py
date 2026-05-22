# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Mark Acceptance Criteria checklists complete for completed managed issues."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path


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


def acceptance_checklist_complete(body: str) -> str:
    """Check every Acceptance Criteria item in an issue body."""

    lines = body.splitlines()
    in_acceptance = False
    saw_item = False
    updated: list[str] = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_acceptance = stripped == "## Acceptance Criteria"
        if in_acceptance:
            left = line.lstrip()
            prefix = line[: len(line) - len(left)]
            if left.startswith("- [ ] "):
                updated.append(f"{prefix}- [x] {left[6:]}")
                saw_item = True
                changed = True
                continue
            if left.lower().startswith("- [x] "):
                saw_item = True
        updated.append(line)
    if not saw_item:
        return body
    rendered = "\n".join(updated)
    if body.endswith("\n"):
        rendered += "\n"
    return rendered if changed else body


def _edit_body(repo: str, number: int, body: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as file_obj:
        file_obj.write(body)
        body_path = Path(file_obj.name)
    try:
        _run_gh(["issue", "edit", str(number), "--repo", repo, "--body-file", str(body_path)])
    finally:
        body_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair Acceptance Criteria checkboxes on completed open issues."
    )
    parser.add_argument("--repo", default="ProjectCuillin/nats-sinks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    raw = _run_gh(
        [
            "issue",
            "list",
            "--repo",
            args.repo,
            "--state",
            "open",
            "--label",
            "completed",
            "--json",
            "number,body",
            "--limit",
            "200",
        ],
        capture_json=True,
    )
    if not isinstance(raw, list):
        raise SystemExit("Unexpected GitHub CLI issue list response.")

    changed = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        number = int(item["number"])
        body = str(item.get("body", ""))
        updated = acceptance_checklist_complete(body)
        if updated == body:
            continue
        changed += 1
        if args.dry_run:
            sys.stdout.write(f"would update issue #{number}\n")
        else:
            _edit_body(args.repo, number, updated)
    sys.stdout.write(f"Acceptance criteria updated for {changed} issue(s).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
