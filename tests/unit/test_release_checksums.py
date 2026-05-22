# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for release checksum manifest generation."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate-checksums.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_checksums", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_checksums"] = module
    spec.loader.exec_module(module)
    return module


def test_artifact_paths_and_checksum_manifest_use_release_asset_names(tmp_path: Path) -> None:
    script = _load_script()
    dist = tmp_path / "dist"
    sbom = dist / "sbom"
    sbom.mkdir(parents=True)
    wheel = dist / "nats_sinks-0.4.0-py3-none-any.whl"
    sdist = dist / "nats_sinks-0.4.0.tar.gz"
    sbom_json = sbom / "nats-sinks-0.4.0.cyclonedx.json"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    sbom_json.write_bytes(b"sbom")

    paths = script.artifact_paths(dist)
    rendered = script.render_checksum_lines(paths)

    assert paths == [sbom_json, wheel, sdist]
    assert f"{hashlib.sha256(b'wheel').hexdigest()}  {wheel.name}" in rendered
    assert f"{hashlib.sha256(b'sdist').hexdigest()}  {sdist.name}" in rendered
    assert f"{hashlib.sha256(b'sbom').hexdigest()}  {sbom_json.name}" in rendered
    assert "sbom/" not in rendered


def test_release_workflow_keeps_checksum_manifest_out_of_pypi_upload_artifact() -> None:
    """PyPI publishing should receive only distributions, not release evidence files."""

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "name: distributions" in workflow
    assert "name: checksum-manifest" in workflow
    assert "\n          name: dist\n" not in workflow
    assert "name: distributions\n          path: dist" in workflow
