# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio

from nats_sinks import JetStreamSinkRunner
from nats_sinks.oracle import OracleSink


async def main() -> None:
    sink = OracleSink(
        dsn="localhost:1521/FREEPDB1",
        user="app_user",
        password_env="ORACLE_PASSWORD",  # noqa: S106 - environment variable name, not a secret
        table="NATS_SINK_EVENTS",
        mode="merge",
    )
    runner = JetStreamSinkRunner(
        nats_url="nats://localhost:4222",
        stream="ORDERS",
        consumer="orders-oracle-sink",
        subject="orders.*",
        sink=sink,
    )
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
