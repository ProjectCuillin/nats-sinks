# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ALLOWED_F2T2EA_PHASES = {
    "find",
    "fix",
    "track",
    "target_review",
    "engage_report",
    "assess",
    "unknown",
}

EXAMPLE_DIR = Path("examples/use-cases/defence")


def _load_example(name: str) -> dict[str, Any]:
    loaded = json.loads((EXAMPLE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _mission_metadata(value: dict[str, Any]) -> dict[str, Any]:
    metadata = value["mission_metadata"]
    assert isinstance(metadata, dict)
    return metadata


def _assert_f2t2ea_metadata(metadata: dict[str, Any]) -> None:
    f2t2ea = metadata["f2t2ea"]
    assert isinstance(f2t2ea, dict)
    assert f2t2ea["phase"] in ALLOWED_F2T2EA_PHASES
    assert metadata["schema"] == "nats_sinks.use_case.mission_metadata.v1"
    assert metadata["profile"] == "f2t2ea-event-phase-tagging"
    assert metadata["profile_version"] == 1


def test_f2t2ea_message_example_is_valid_json() -> None:
    example = _load_example("f2t2ea-message.json")

    _assert_f2t2ea_metadata(_mission_metadata(example))
    assert example["event"]["event_id"].startswith("SYN-")


def test_f2t2ea_file_record_example_keeps_phase_in_core_metadata() -> None:
    example = _load_example("f2t2ea-file-record.json")

    _assert_f2t2ea_metadata(_mission_metadata(example))
    assert example["metadata"]["mission_metadata"]["f2t2ea"]["phase"] == "track"
    assert example["labels_list"] == ["synthetic", "mission-test", "f2t2ea-example"]


def test_f2t2ea_oracle_row_example_uses_dedicated_metadata_column() -> None:
    example = _load_example("f2t2ea-oracle-row.json")
    columns = example["columns"]
    assert isinstance(columns, dict)
    metadata = columns["MISSION_METADATA_JSON"]
    assert isinstance(metadata, dict)

    _assert_f2t2ea_metadata(metadata)
    assert columns["LABELS"] == "synthetic;mission-test;f2t2ea-example"
