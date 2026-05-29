# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Release-gate lint compatibility regressions."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_container_e2e_helpers_use_version_compatible_s603_suppressions() -> None:
    """Keep subprocess suppressions compatible across local and CI Ruff."""

    for relative_path in (
        "scripts/run-coherence-sink-e2e.py",
        "scripts/run-oracle-nosql-sink-e2e.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")

        assert "noqa: S603 -" not in source
        assert "noqa: S603,RUF100" in source


def test_async_multi_sink_flow_avoids_direct_pathlib_mkdir() -> None:
    """Async routing certification should delegate blocking path creation."""

    source = (ROOT / "src/nats_sinks/testing/multi_sink_routing.py").read_text(encoding="utf-8")
    function_start = source.index("async def run_reduced_multi_sink_routing_flow")
    function_end = source.index("\ndef _build_reduced_children", function_start)
    function_body = source[function_start:function_end]

    assert "work_dir.mkdir(" not in function_body
