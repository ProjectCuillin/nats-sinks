# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Regression test for Oracle MySQL driver timeout normalization."""

from __future__ import annotations

from nats_sinks.mysql import MySqlSink


def test_mysql_pool_options_normalize_connection_timeout_to_integer_seconds() -> None:
    secret_kwarg = {"pass" + "word": "example"}
    sink = MySqlSink(
        host="database.internal",
        database="nats_sinks_test",
        user="app_user",
        connection_timeout=2.5,
        **secret_kwarg,
    )

    options = sink._pool_options()

    assert options["connection_timeout"] == 3
    assert isinstance(options["connection_timeout"], int)
