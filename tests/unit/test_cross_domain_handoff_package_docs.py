# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

DOCS_DIR = Path("docs")
PACKAGE_DOC = DOCS_DIR / "use-cases" / "defence" / "cross-domain-handoff-package.md"
PACKAGE_DIR = Path("examples/use-cases/defence/cross-domain-handoff-package")
MANIFEST_PATH = PACKAGE_DIR / "manifest.json"

_SAFE_PACKAGE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,126}[a-z0-9]$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_IP_LITERAL = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_BLOCKED_VALUE_TERMS = (
    "password",
    "private_key",
    "credential",
    "connection string",
    "wallet_",
    "bearer ",
    "token=",
    "http://",
    "https://",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, dict)
    return data


def _walk_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for key, nested in value.items():
            values.append(str(key))
            values.extend(_walk_values(nested))
        return values
    if isinstance(value, list):
        values = []
        for nested in value:
            values.extend(_walk_values(nested))
        return values
    if isinstance(value, str):
        return [value]
    return []


def _assert_package_path_is_safe(path_value: str) -> Path:
    """Package paths are relative manifest references, never trusted OS paths."""
    assert path_value
    assert not path_value.startswith(("/", "\\"))
    assert "\\" not in path_value
    assert "\x00" not in path_value
    path = Path(path_value)
    assert not path.is_absolute()
    assert ".." not in path.parts
    resolved = (PACKAGE_DIR / path).resolve()
    assert resolved.is_relative_to(PACKAGE_DIR.resolve())
    return PACKAGE_DIR / path


def test_cross_domain_package_docs_are_publicly_discoverable() -> None:
    """The package blueprint must be linked from the public documentation tree."""
    content = _read(PACKAGE_DOC)
    use_cases_index = _read(DOCS_DIR / "use-cases" / "index.md")
    defence_index = _read(DOCS_DIR / "use-cases" / "defence" / "index.md")
    preparation_page = _read(
        DOCS_DIR / "use-cases" / "defence" / "cross-domain-handoff-preparation.md"
    )
    mkdocs_config = _read(Path("mkdocs.yml"))

    assert "```mermaid" in content
    assert "not a cross-domain guard" in content
    assert "certification boundary" in content
    assert "Commit first. ACK last. Design for redelivery." in content
    assert "(defence/cross-domain-handoff-package.md)" in use_cases_index
    assert "(cross-domain-handoff-package.md)" in defence_index
    assert "(cross-domain-handoff-package.md)" in preparation_page
    assert "Cross-Domain Handoff Package: use-cases/defence/cross-domain-handoff-package.md" in (
        mkdocs_config
    )


def test_example_manifest_has_bounded_review_package_shape() -> None:
    """The tracked package example should model a bounded, reviewable artifact."""
    manifest = _load_json(MANIFEST_PATH)

    assert manifest["schema"] == "nats_sinks.cross_domain_handoff_package.v1"
    assert manifest["package_format"] == "directory"
    assert _SAFE_PACKAGE_ID.fullmatch(manifest["package_id"])
    assert isinstance(manifest["created_epoch_ns"], int)
    assert manifest["review"]["approval_state"] == "pending_review"
    assert manifest["review"]["release_required"] is True
    assert manifest["metadata"]["classification"] == "NATO SECRET"
    assert manifest["metadata"]["priority"] == "high"
    assert manifest["metadata"]["labels"] == "review-candidate;mission-test"
    assert manifest["metadata"]["labels_list"] == ["review-candidate", "mission-test"]
    assert manifest["payload"]["encrypted"] is True
    assert manifest["payload"]["algorithm"] == "AES-256-GCM"
    assert manifest["limits"]["max_files"] >= len(manifest["files"])
    assert MANIFEST_PATH.stat().st_size <= manifest["limits"]["max_manifest_bytes"]


def test_example_manifest_paths_sizes_and_hashes_match_package_files() -> None:
    """Manifest paths should stay in the package and hashes should be verifiable."""
    manifest = _load_json(MANIFEST_PATH)
    package_total = MANIFEST_PATH.stat().st_size

    seen_paths: set[str] = set()
    for file_entry in manifest["files"]:
        relative_path = file_entry["path"]
        assert relative_path not in seen_paths
        seen_paths.add(relative_path)

        file_path = _assert_package_path_is_safe(relative_path)
        assert file_path.is_file()
        file_bytes = file_path.read_bytes()
        package_total += len(file_bytes)

        assert len(file_bytes) == file_entry["size_bytes"]
        assert len(file_bytes) <= manifest["limits"]["max_package_bytes"]
        assert _SHA256_HEX.fullmatch(file_entry["sha256"])
        assert hashlib.sha256(file_bytes).hexdigest() == file_entry["sha256"]

    payload_path = _assert_package_path_is_safe(manifest["payload"]["path"])
    payload_bytes = payload_path.read_bytes()
    assert len(payload_bytes) == manifest["payload"]["size_bytes"]
    assert hashlib.sha256(payload_bytes).hexdigest() == manifest["payload"]["sha256"]
    assert package_total <= manifest["limits"]["max_package_bytes"]


def test_example_manifest_custody_hashes_match_file_entries() -> None:
    """Custody summaries should repeat the exact file hashes from the manifest."""
    manifest = _load_json(MANIFEST_PATH)
    hashes_by_role = {entry["role"]: entry["sha256"] for entry in manifest["files"]}

    assert manifest["custody"]["hash_algorithm"] == "sha256"
    assert manifest["custody"]["payload_sha256"] == hashes_by_role["payload"]
    assert manifest["custody"]["metadata_sha256"] == hashes_by_role["metadata"]
    assert manifest["custody"]["evidence_sha256"] == hashes_by_role["evidence"]


def test_example_package_does_not_contain_obvious_secret_or_endpoint_material() -> None:
    """Public fixtures must stay sanitized and safe for issue comments and docs."""
    for path in PACKAGE_DIR.glob("*.json"):
        data = _load_json(path)
        text_values = "\n".join(_walk_values(data)).lower()

        assert not _IP_LITERAL.search(text_values)
        for blocked in _BLOCKED_VALUE_TERMS:
            assert blocked not in text_values
