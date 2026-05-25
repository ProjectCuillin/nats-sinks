# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression test for reviewed Oracle MySQL dynamic SQL annotations."""

from __future__ import annotations

from pathlib import Path


def test_mysql_upsert_sql_line_has_bandit_review_annotation() -> None:
    source = Path("src/nats_sinks/mysql/sql.py").read_text(encoding="utf-8")
    flagged_lines = [
        line
        for line in source.splitlines()
        if 'insert into {quoted_table_name} ({column_list}) values ({placeholders}) "' in line
    ]

    assert flagged_lines
    assert all("# noqa: S608" in line and "# nosec B608" in line for line in flagged_lines)
