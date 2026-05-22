# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

DOCS_DIR = Path("docs")
MISSION_SUPPORT_DIR = DOCS_DIR / "use-cases" / "mission-support"

SCENARIOS = {
    "restricted-event-storage.md": "Restricted Event Storage",
    "disconnected-file-handoff.md": "Disconnected File Handoff",
    "dlq-triage-and-replay.md": "DLQ Triage And Replay Preparation",
    "destination-outage-recovery.md": "Destination Outage Recovery",
}

REQUIRED_SECTIONS = (
    "## Generic Framework Behavior",
    "## Configuration",
    "## Sink-Specific Choices",
    "## Operational Flow",
    "## Failure Behavior",
    "## Test Guidance",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_mission_support_scenarios_are_discoverable() -> None:
    """Operational examples must remain visible from public documentation entry points."""
    use_cases_index = _read(DOCS_DIR / "use-cases" / "index.md")
    mission_index = _read(MISSION_SUPPORT_DIR / "index.md")
    docs_home = _read(DOCS_DIR / "index.md")
    readme = _read(Path("README.md"))
    mkdocs_config = _read(Path("mkdocs.yml"))

    assert "(use-cases/mission-support/index.md)" in docs_home
    assert "docs/use-cases/mission-support/index.md" in readme
    assert "Mission-Support Operational Examples: use-cases/mission-support/index.md" in (
        mkdocs_config
    )

    for filename, title in SCENARIOS.items():
        assert (MISSION_SUPPORT_DIR / filename).is_file()
        assert f"(mission-support/{filename})" in use_cases_index
        assert f"({filename})" in mission_index
        assert f"{title}: use-cases/mission-support/{filename}" in mkdocs_config


def test_mission_support_scenarios_have_required_operational_sections() -> None:
    """Each scenario should explain configuration, flow, failures, and validation."""
    for filename in SCENARIOS:
        content = _read(MISSION_SUPPORT_DIR / filename)

        for section in REQUIRED_SECTIONS:
            assert section in content
        assert "```mermaid" in content
        assert "ACK" in content
        assert "nats-sink" in content or "pytest" in content


def test_mission_support_pages_keep_generic_and_sink_specific_guidance_separate() -> None:
    """The examples should compose existing capabilities rather than define a new mode."""
    index = _read(MISSION_SUPPORT_DIR / "index.md")
    assert "not new runtime modes" in index
    assert "generic framework" in index

    for filename in SCENARIOS:
        content = _read(MISSION_SUPPORT_DIR / filename)
        assert "Oracle" in content
        assert "File" in content or "file sink" in content.lower()
        assert "Generic Framework Behavior" in content
        assert "Sink-Specific Choices" in content
