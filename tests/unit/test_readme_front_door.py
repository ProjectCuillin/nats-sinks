# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the README front-door structure."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_front_door_keeps_expected_section_order() -> None:
    """The README should stay short while keeping first-reader landmarks."""

    readme = _read(REPO_ROOT / "README.md")
    headings = [
        "## Overview",
        "## Available Sinks",
        "## Five-Minute Local MVP Demo",
        "## Architecture",
        "## Status",
        "## Available Today",
        "## Installation",
    ]

    offsets = [readme.index(heading) for heading in headings]

    assert offsets == sorted(offsets)


def test_readme_lists_production_and_experimental_sinks() -> None:
    """The front door should separate production sinks from evaluation sinks."""

    readme = _read(REPO_ROOT / "README.md")

    for production_sink in (
        "Oracle Database",
        "Oracle MySQL",
        "File",
        "Edge Spool",
        "HTTP",
        "S3-Compatible Object Storage",
    ):
        assert production_sink in readme

    assert "Experimental and certification-stage sinks" in readme
    assert "Palantir Foundry" in readme
    assert "Palantir Gotham" in readme
    assert "experimental sink" in readme


def test_readme_links_short_demo_and_detailed_status_pages() -> None:
    """The README should link outward instead of carrying the full inventory."""

    readme = _read(REPO_ROOT / "README.md")

    assert "scripts/run-local-mvp-demo.sh" in readme
    assert "https://nats-sinks.readthedocs.io/en/latest/getting-started/" in readme
    assert "https://nats-sinks.readthedocs.io/en/latest/project-status/" in readme
    assert "https://nats-sinks.readthedocs.io/en/latest/available-today/" in readme
    assert (
        "https://github.com/ProjectCuillin/nats-sinks/blob/main/docs/use-cases/"
        "mission-support/index.md" in readme
    )


def test_local_mvp_demo_helper_is_scoped_to_disposable_local_use() -> None:
    """The one-command helper should avoid privileged package installation."""

    script = _read(REPO_ROOT / "scripts" / "run-local-mvp-demo.sh")

    assert "SPDX-License-Identifier: Apache-2.0" in script
    assert "set -eu" in script
    assert "NATS_SINKS_DEMO_REF:-main" in script
    assert "NATS_SINKS_DEMO_KEEP_RUNNING:-1" in script
    assert "docker compose version" in script
    assert 'git clone --depth 1 --branch "$REF"' in script
    assert "run-docker-local-smoke.py" in script
    assert "sudo " not in script
    assert "apt " not in script
    assert "dnf " not in script
    assert "yum " not in script
    assert "docker login" not in script


def test_mkdocs_navigation_exposes_front_door_detail_pages() -> None:
    """The moved README detail pages must stay visible in public docs nav."""

    mkdocs_config = _read(REPO_ROOT / "mkdocs.yml")

    assert "Available Today: available-today.md" in mkdocs_config
    assert "Project Status: project-status.md" in mkdocs_config
    assert "Getting Started: getting-started.md" in mkdocs_config
