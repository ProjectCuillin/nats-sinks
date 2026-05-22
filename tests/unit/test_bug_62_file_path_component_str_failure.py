# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from nats_sinks.file.mapping import safe_path_component


class RaisesOnText:
    """Synthetic hostile value whose string conversion fails."""

    def __str__(self) -> str:
        raise RuntimeError("synthetic string conversion failure")


def test_safe_path_component_handles_failed_string_conversion() -> None:
    """The file path sanitizer must not crash on hostile object conversion."""

    component = safe_path_component(RaisesOnText(), fallback="subject")

    assert component.startswith("subject-unrenderable-")
    assert "/" not in component
    assert "\\" not in component
    assert component not in {".", ".."}
