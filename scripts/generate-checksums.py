# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Generate SHA-256 release checksums for package and SBOM artifacts.

The release workflow uploads wheels, source distributions, SBOMs, and the
checksum manifest to GitHub Releases. This script keeps that evidence
dependency-free and deterministic so maintainers can reproduce the same file
locally before pushing a tag.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

DEFAULT_OUTPUT_NAME = "SHA256SUMS"


def artifact_paths(dist_dir: Path) -> list[Path]:
    """Return release artifacts that should be represented in SHA256SUMS."""

    patterns = ("*.whl", "*.tar.gz", "sbom/*")
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(path for path in dist_dir.glob(pattern) if path.is_file())
    return sorted(paths, key=lambda path: path.name)


def sha256_file(path: Path) -> str:
    """Hash a release artifact without loading the whole file into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_checksum_lines(paths: list[Path]) -> str:
    """Render a POSIX-style checksum manifest using release asset names."""

    return "".join(f"{sha256_file(path)}  {path.name}\n" for path in paths)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SHA-256 release checksums.")
    parser.add_argument(
        "dist_dir",
        type=Path,
        nargs="?",
        default=Path("dist"),
        help="Distribution directory containing package artifacts.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Checksum manifest path. Defaults to <dist_dir>/SHA256SUMS.",
    )
    args = parser.parse_args()

    dist_dir = args.dist_dir
    output = args.output or dist_dir / DEFAULT_OUTPUT_NAME
    paths = artifact_paths(dist_dir)
    if not paths:
        raise SystemExit(f"No release artifacts found under {dist_dir}.")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_checksum_lines(paths), encoding="utf-8")
    sys.stdout.write(f"Generated checksum manifest: {output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
