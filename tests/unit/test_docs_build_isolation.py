# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for collision-safe documentation build automation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_docs_check_script_uses_isolated_site_directories() -> None:
    """The docs helper must not let two MkDocs builds clean the same output tree."""

    script = _read("scripts/check-docs.sh")

    assert "mktemp -d" in script
    assert '--site-dir "$READTHEDOCS_SITE_DIR"' in script
    assert '--site-dir "$GITHUB_PAGES_SITE_DIR"' in script
    assert "NATS_SINKS_DOCS_BUILD_ROOT" in script
    assert 'rm -rf "$READTHEDOCS_SITE_DIR" "$GITHUB_PAGES_SITE_DIR"' in script
    assert "mkdocs build --strict\n" not in script


def test_release_and_ci_checks_use_docs_wrapper() -> None:
    """Release-facing automation should use the collision-safe docs helper."""

    for relative_path in (
        "scripts/check.sh",
        ".github/workflows/ci.yml",
        ".github/workflows/docs.yml",
        ".github/workflows/release.yml",
    ):
        text = _read(relative_path)
        assert "scripts/check-docs.sh" in text, relative_path
        assert "mkdocs build --strict" not in text, relative_path


def test_github_pages_workflow_is_single_artifact_build() -> None:
    """Pages still writes one uploadable `site/` artifact, not two competing builds."""

    workflow = _read(".github/workflows/pages.yml")

    assert workflow.count("mkdocs build --strict") == 1
    assert "actions/upload-pages-artifact" in workflow
    assert "path: site" in workflow
