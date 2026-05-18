# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio

import nats


async def main() -> None:
    nc = await nats.connect("nats://localhost:4222")
    await nc.publish("orders.created", b'{"order_id":"O-1001","amount":42.5}')
    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
