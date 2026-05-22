# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression test for managed bug report issue #61.

The managed bug comment helper can attach small test files from `tests/` and
`scripts/` to public bug discussions. Shell scripts should not be displayed as
Python code because that makes the public regression evidence misleading.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "comment-bug-issue.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("comment_bug_issue", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["comment_bug_issue"] = module
    spec.loader.exec_module(module)
    return module


def test_shell_script_attachment_uses_shell_markdown_fence() -> None:
    script = _load_script()
    bug_sync = script._load_bug_sync_script()
    sync = bug_sync._load_sync_script()

    rendered = script._read_test_file(sync, ROOT / "scripts" / "security.sh")

    assert "```sh\n" in rendered
    assert "```python\n" not in rendered


def test_python_attachment_continues_to_use_python_markdown_fence() -> None:
    script = _load_script()
    bug_sync = script._load_bug_sync_script()
    sync = bug_sync._load_sync_script()

    rendered = script._read_test_file(sync, Path(__file__))

    assert "```python\n" in rendered


def test_unknown_attachment_extension_uses_plain_text_markdown_fence() -> None:
    script = _load_script()
    bug_sync = script._load_bug_sync_script()
    sync = bug_sync._load_sync_script()

    rendered = script._read_test_file(
        sync,
        ROOT / "tests" / "fixtures" / "payloads" / "invalid-json.txt",
    )

    assert "```text\n" in rendered
    assert "```python\n" not in rendered
