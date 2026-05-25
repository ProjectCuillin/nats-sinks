# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Shared Oracle connection option construction.

OracleSink and read-only Oracle helper tools use the same validated
`OracleSinkConfig` model.  Keeping pool option construction in one small module
prevents subtle drift between write paths and operator inspection paths while
still resolving secrets only at the last possible moment.

The returned dictionary is intended for `oracledb.create_pool`.  Callers must
never log it because it contains the resolved Oracle password and, when
configured, the resolved wallet password.
"""

from __future__ import annotations

from typing import Any

from nats_sinks.oracle.config import OracleSinkConfig


def build_oracle_pool_options(config: OracleSinkConfig) -> dict[str, Any]:
    """Build `oracledb.create_pool` options from validated Oracle config.

    The helper intentionally exposes only explicit python-oracledb options that
    nats-sinks supports.  It does not pass arbitrary user-provided keyword
    arguments through to the driver.
    """

    optional_options = {
        "config_dir": config.config_dir,
        "wallet_location": config.wallet_location,
        "wallet_password": config.resolve_wallet_password(),
        "ssl_server_dn_match": config.ssl_server_dn_match,
        "ssl_server_cert_dn": config.ssl_server_cert_dn,
        "tcp_connect_timeout": config.tcp_connect_timeout,
        "retry_count": config.retry_count,
        "retry_delay": config.retry_delay,
        "https_proxy": config.https_proxy,
        "https_proxy_port": config.https_proxy_port,
    }
    return {
        key: value
        for key, value in {
            "user": config.user,
            "password": config.resolve_password(),
            "dsn": config.dsn,
            "min": config.pool_min,
            "max": config.pool_max,
            "increment": config.pool_increment,
            **optional_options,
        }.items()
        if value is not None
    }
