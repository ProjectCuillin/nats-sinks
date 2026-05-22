#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Check Markdown links that must render correctly outside the repository.

PyPI renders `README.md` as the package description. Relative links such as
`docs/oracle-sink.md` work on GitHub and MkDocs, but they do not resolve
correctly from the PyPI project page. This repository check therefore enforces
fully qualified links for PyPI-facing Markdown files while allowing relative
links inside `docs/`, where MkDocs and Read the Docs use them for
version-local navigation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWED_PREFIXES = ("https://", "http://", "mailto:", "#")
PYPI_RENDERED_FILES = {Path("README.md")}

LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _markdown_files(root: Path) -> list[Path]:
    """Return Markdown files that are rendered outside the docs site.

    The docs tree intentionally uses relative links so Read the Docs can keep
    readers inside the current documentation version. The files in
    `PYPI_RENDERED_FILES` are rendered outside that MkDocs context, so those
    files need stricter checks.
    """

    return sorted(path for path in PYPI_RENDERED_FILES if (root / path).exists())


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
    """Return a non-zero exit code when PyPI-facing relative links are found."""

    findings: list[str] = []
    for path in _markdown_files(Path(".")):
        findings.extend(_relative_link_findings(path))

    if findings:
        sys.stdout.write(
            "Relative Markdown links are not allowed in PyPI-facing files; "
            "use public documentation or repository URLs instead.\n"
        )
        sys.stdout.write("Documentation base URL: https://nats-sinks.readthedocs.io/en/latest/\n")
        sys.stdout.write("Repository base URL: https://github.com/ProjectCuillin/nats-sinks/\n")
        for finding in findings:
            sys.stdout.write(f"{finding}\n")
        return 1

    sys.stdout.write(
        "PyPI-facing Markdown links use fully qualified URLs, mailto links, or anchors.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
