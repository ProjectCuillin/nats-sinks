#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Validate release-version consistency across package metadata and docs.

The package version appears in a few places that different audiences see:
`pyproject.toml` drives packaging, `nats_sinks.__version__` drives the CLI,
the README is rendered by PyPI, and the documentation home page introduces the
current release.  Keeping those values aligned is release hygiene and avoids
operator confusion during upgrades.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    if not isinstance(version, str) or not version:
        raise ValueError("project.version must be a non-empty string")
    return version


def _module_version() -> str:
    source = (ROOT / "src/nats_sinks/__init__.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    raise ValueError("src/nats_sinks/__init__.py must define __version__")


def _require_text(path: str, expected: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if expected not in text:
        raise ValueError(f"{path} does not contain expected text: {expected}")


def main() -> int:
    version = _project_version()
    module_version = _module_version()
    errors: list[str] = []

    if module_version != version:
        errors.append(
            f"src/nats_sinks/__init__.py __version__ is {module_version!r}, "
            f"but pyproject.toml project.version is {version!r}"
        )

    for path in ("README.md", "docs/index.md"):
        try:
            _require_text(path, f"The current release is `{version}`.")
        except ValueError as exc:
            errors.append(str(exc))

    try:
        _require_text("CHANGELOG.md", f"## [{version}]")
    except ValueError as exc:
        errors.append(str(exc))

    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9_.+-]+)?", version):
        errors.append(f"project.version does not look like a PEP 440 release: {version}")

    if errors:
        for error in errors:
            sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"Version metadata is consistent for {version}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
