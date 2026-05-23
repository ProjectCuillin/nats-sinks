# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

DOCS_DIR = Path("docs")
DEFENCE_DIR = DOCS_DIR / "use-cases" / "defence"
EXAMPLE_DIR = Path("examples") / "use-cases" / "defence"

BLUEPRINTS = {
    "f2t2ea-event-phase-tagging.md": "F2T2EA Event Phase Tagging",
    "sensor-event-custody.md": "Sensor Event Custody",
    "classification-and-labels.md": "Classification And Labels",
    "chain-of-custody.md": "Chain Of Custody",
    "cross-domain-handoff-preparation.md": "Cross-Domain Handoff Preparation",
    "cross-domain-handoff-package.md": "Cross-Domain Handoff Package",
    "edge-operation.md": "Edge Operation",
    "audit-oriented-persistence.md": "Audit-Oriented Persistence",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_defence_blueprint_pages_are_linked_from_indexes_and_mkdocs() -> None:
    """Keep the public blueprint map discoverable from all documentation entry points."""
    use_cases_index = _read(DOCS_DIR / "use-cases" / "index.md")
    defence_index = _read(DEFENCE_DIR / "index.md")
    mkdocs_config = _read(Path("mkdocs.yml"))

    for filename, title in BLUEPRINTS.items():
        assert (DEFENCE_DIR / filename).is_file()
        assert f"({filename})" in defence_index
        assert f"(defence/{filename})" in use_cases_index
        assert f"{title}: use-cases/defence/{filename}" in mkdocs_config


def test_defence_blueprints_include_diagrams_and_safety_boundary() -> None:
    """The blueprint pages should explain the model and its non-goals clearly."""
    for filename in BLUEPRINTS:
        content = _read(DEFENCE_DIR / filename)

        assert "```mermaid" in content
        assert "targeting system" in content
        assert "weapons-release" in content
        assert "commit" in content.lower() or "ack" in content.lower()


def test_defence_blueprint_examples_cover_oracle_and_file_metadata_shapes() -> None:
    """Examples must show mission metadata in both current production sinks."""
    oracle_row = json.loads((EXAMPLE_DIR / "f2t2ea-oracle-row.json").read_text(encoding="utf-8"))
    file_record = json.loads((EXAMPLE_DIR / "f2t2ea-file-record.json").read_text(encoding="utf-8"))

    assert oracle_row["columns"]["MISSION_METADATA_JSON"]["profile"] == (
        "f2t2ea-event-phase-tagging"
    )
    assert file_record["mission_metadata"]["profile"] == ("f2t2ea-event-phase-tagging")
    assert file_record["metadata"]["mission_metadata"]["profile"] == ("f2t2ea-event-phase-tagging")
