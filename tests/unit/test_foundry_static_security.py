# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path


def test_foundry_http_urlopen_has_reviewed_bandit_suppression() -> None:
    source = Path("src/nats_sinks/foundry/client.py").read_text(encoding="utf-8")
    urlopen_lines = [line for line in source.splitlines() if "request.urlopen(" in line]

    assert urlopen_lines
    assert all("# nosec B310" in line for line in urlopen_lines)
