"""Redis pub/sub → WebSocket broadcast listener (the realtime backbone)."""
import asyncio
import json
import logging

import redis.asyncio as aioredis

from backend.core import config, state

log = logging.getLogger(__name__)


async def _pubsub_listener() -> None:
    tick_channels = [f"mt5:tick:{s}" for s in config.SYMBOLS]

    while True:
        r = aioredis.Redis(connection_pool=state.redis_pool)
        pubsub = r.pubsub()
        try:
            await pubsub.subscribe(*tick_channels, "mt5:ai_analysis")
            log.info("Subscribed to Redis pub/sub (tick + ai_analysis)")
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                channel = msg["channel"]
                try:
                    data = json.loads(msg["data"])
                    if channel == "mt5:ai_analysis":
                        await state.manager.broadcast(data["symbol"], "__ai__", data)
                    else:
                        symbol = channel.rsplit(":", 1)[-1]
                        await state.manager.broadcast(symbol, data["resolution"], data["bar"])
                except Exception:
                    log.exception(f"pub/sub message handling failed on {channel!r}")
        except asyncio.CancelledError:
            await pubsub.aclose()
            await r.aclose()
            raise
        except Exception:
            log.exception("Redis pub/sub listener dropped — reconnecting in 2s")
            try:
                await pubsub.aclose()
                await r.aclose()
            except Exception:
                pass
            await asyncio.sleep(2)
