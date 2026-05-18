#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check Markdown links that must render correctly outside the repository.

PyPI renders `README.md` as the package description. Relative links such as
`docs/oracle-sink.md` work on GitHub, but they do not resolve correctly from
the PyPI project page. This small repository check keeps Markdown links
portable by requiring normal links to use fully qualified URLs, mail links, or
same-page anchors.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWED_PREFIXES = ("https://", "http://", "mailto:", "#")
SKIPPED_DIRS = {
    ".git",
    ".local",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "site",
}

LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _markdown_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.md"):
        if any(part in SKIPPED_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def _is_allowed_target(target: str) -> bool:
    normalized = target.strip("<>")
    return normalized.startswith(ALLOWED_PREFIXES)


def _relative_link_findings(path: Path) -> list[str]:
    findings: list[str] = []
    in_fence = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in LINK_PATTERN.finditer(line):
            target = match.group(1)
            if not _is_allowed_target(target):
                findings.append(f"{path}:{line_number}: relative Markdown link {target!r}")
    return findings


def main() -> int:
    """Return a non-zero exit code when relative Markdown links are found."""

    findings: list[str] = []
    for path in _markdown_files(Path(".")):
        findings.extend(_relative_link_findings(path))

    if findings:
        sys.stdout.write("Relative Markdown links are not allowed; use full GitHub URLs instead.\n")
        sys.stdout.write("Base URL: https://github.com/ProjectCuillin/nats-sinks/blob/main/\n")
        for finding in findings:
            sys.stdout.write(f"{finding}\n")
        return 1

    sys.stdout.write("Markdown links use fully qualified URLs, mailto links, or anchors.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
