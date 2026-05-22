# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the high-confidence local secret scanner.

The scanner is intentionally dependency-light because it runs in pre-commit,
local release checks, and GitHub Actions.  GitHub-hosted runners do not always
ship with `ripgrep`, so the script must retain a conservative `grep` fallback
instead of making the entire CI security stage depend on one local developer
tool being present.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_secret_scan_falls_back_to_grep_when_ripgrep_is_unavailable(tmp_path: Path) -> None:
    """A minimal checkout can run the scanner with only POSIX shell and grep."""

    shell = shutil.which("sh")
    grep = shutil.which("grep")
    if shell is None or grep is None:  # pragma: no cover - platform guard
        pytest.skip("the fallback test requires sh and grep")

    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "secret-scan.sh", scripts / "secret-scan.sh")
    (checkout / "README.md").write_text("public documentation only\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    os.symlink(grep, bin_dir / "grep")

    result = subprocess.run(  # noqa: S603 - fixed shell executable and test script path.
        [shell, "scripts/secret-scan.sh"],
        cwd=checkout,
        env={**os.environ, "PATH": str(bin_dir)},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "No high-confidence secret material found." in result.stdout
