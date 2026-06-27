"""Webhook endpoints (Worker → Server) — live/closed/quote ticks + backfill batch."""
import asyncio
import json
import logging
import time
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends

from backend.core import config, state
from backend.core.auth import verify_webhook
from backend.models import BatchBarsPayload, TickPayload
from backend.db.postgres import _upsert_bars_pg

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/webhook/tick/{symbol}")
async def webhook_tick(
    symbol: str,
    payload: TickPayload,
    _: Annotated[None, Depends(verify_webhook)],
):
    r = aioredis.Redis(connection_pool=state.redis_pool)
    now_ms = int(time.time() * 1000)

    pipe = r.pipeline()

    for res, bar in payload.live.items():
        bar_dict = bar.model_dump()
        bar_dict["time"] = int(bar_dict["time"])
        live_key = f"mt5:live:{symbol}:{res}"
        pipe.hset(live_key, mapping=bar_dict)
        pipe.expire(live_key, 30)
        pipe.publish(f"mt5:tick:{symbol}", json.dumps({"resolution": res, "bar": bar_dict}))

    closed_by_res: dict[str, list[dict]] = {}
    for res, bar in (payload.closed or {}).items():
        bar_dict = bar.model_dump()
        bars_key = f"mt5:bars:{symbol}:{res}"
        pipe.zadd(bars_key, {json.dumps(bar_dict): bar_dict["time"]}, nx=True)
        pipe.zremrangebyscore(bars_key, "-inf", now_ms - config.CUTOFF_MS)
        pipe.expire(bars_key, config.TTL_SECONDS)
        closed_by_res[res] = [bar_dict]

    if payload.quote:
        pipe.hset(f"mt5:quote:{symbol}", mapping=payload.quote.model_dump())
        pipe.expire(f"mt5:quote:{symbol}", 30)

    await pipe.execute()

    # PG upsert closed bars (fire-and-forget)
    for res, bars in closed_by_res.items():
        asyncio.create_task(_upsert_bars_pg(symbol, res, bars))

    # Mark closed bars → AI monitor loop will pick these up
    if closed_by_res:
        pipe2 = r.pipeline()
        for res in closed_by_res:
            pipe2.set(f"mt5:candle_closed:{symbol}:{res}", "1", ex=120)
        await pipe2.execute()

    return {"ok": True}


@router.post("/webhook/bars/batch")
async def webhook_bars_batch(
    payload: BatchBarsPayload,
    _: Annotated[None, Depends(verify_webhook)],
):
    """Nhận batch bars từ worker khi backfill lịch sử lúc khởi động."""
    r = aioredis.Redis(connection_pool=state.redis_pool)
    bars_key = f"mt5:bars:{payload.symbol}:{payload.resolution}"

    pipe = r.pipeline(transaction=False)
    bar_dicts = []
    for bar in payload.bars:
        bar_dict = bar.model_dump()
        pipe.zadd(bars_key, {json.dumps(bar_dict): bar_dict["time"]}, nx=True)
        bar_dicts.append(bar_dict)

    pipe.expire(bars_key, config.TTL_SECONDS)
    await pipe.execute()

    # PG upsert batch (background)
    asyncio.create_task(_upsert_bars_pg(payload.symbol, payload.resolution, bar_dicts))

    log.info(f"Backfill {payload.symbol}:{payload.resolution} +{len(payload.bars)} bars")
    return {"ok": True, "count": len(payload.bars)}
