# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the local GitHub authentication release helper."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-gh-auth.sh"


def _fake_gh(tmp_path: Path, body: str) -> Path:
    gh = tmp_path / "gh"
    gh.write_text(body, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    return gh


def _env_with_fake_gh(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return env


def test_check_gh_auth_accepts_authenticated_api_probe(
    tmp_path: Path,
) -> None:
    """A usable authenticated API probe is enough for release commands."""

    _fake_gh(
        tmp_path,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  exit 1
fi
if [[ "$1" == "api" && "$2" == "--hostname" && "$4" == "user" && "$5" == "--silent" ]]; then
  exit 0
fi
exit 2
""",
    )

    result = subprocess.run(  # noqa: S603 - fixed repo script with fake PATH.
        [str(SCRIPT), "--check-only"],
        cwd=ROOT,
        env=_env_with_fake_gh(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "authentication is valid" in result.stdout
    assert "token" not in result.stdout.lower()


def test_check_gh_auth_fails_when_status_and_api_probe_fail(tmp_path: Path) -> None:
    """The helper must still fail closed when no authenticated path works."""

    _fake_gh(
        tmp_path,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  exit 1
fi
if [[ "$1" == "api" ]]; then
  exit 1
fi
exit 2
""",
    )

    result = subprocess.run(  # noqa: S603 - fixed repo script with fake PATH.
        [str(SCRIPT), "--check-only"],
        cwd=ROOT,
        env=_env_with_fake_gh(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "authentication is not valid" in result.stderr
