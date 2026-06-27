"""User-position sync endpoint — mirror locked Long/Short positions + refresh AI verdict."""
import asyncio
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, Body

from backend.core import state
from backend.ai.analyzer import _analyze_single
from backend.positions import (
    _position_outcome, _position_status, _reconcile_user_positions,
)

router = APIRouter()


@router.post("/api/user_position")
async def sync_user_positions(symbol: str, resolution: str, payload: dict = Body(...)):
    """Sync the user's LOCKED Long/Short positions for this (symbol, resolution).
    Body: {positions:[{side,entry,stop,target,entry_time_ms}]} — only locked ones.

    - Persists every locked position to the user_position table (entered orders).
    - 'Open' = live price still inside the box (between SL and TP). Once price
      crosses TP/SL the position is no longer open.
    - The AI manages the OPEN position whose entry is nearest to now (most recent).
      No open position → AI stops managing (active trade cleared)."""
    raw_positions = payload.get("positions") or []
    norm: list[dict] = []
    for p in raw_positions:
        if (p.get("shape_id")
                and p.get("side") in ("BUY", "SELL")
                and isinstance(p.get("entry"), (int, float))):
            norm.append({
                "shape_id":      str(p["shape_id"]),
                "side":          p["side"],
                "entry":         float(p["entry"]),
                "stop":          float(p["stop"])   if p.get("stop")   is not None else None,
                "target":        float(p["target"]) if p.get("target") is not None else None,
                "entry_time_ms": int(p["entry_time_ms"]) if p.get("entry_time_ms") is not None else None,
            })

    r = aioredis.Redis(connection_pool=state.redis_pool)
    quote_raw = await r.hgetall(f"mt5:quote:{symbol}")
    bid = float(quote_raw["bid"]) if quote_raw and "bid" in quote_raw else None

    # Status = "ever touched TP/SL" (bar high/low since entry) OR live price now.
    for p in norm:
        out = await _position_outcome(symbol, resolution, p["side"],
                                      p.get("entry_time_ms"), p.get("stop"), p.get("target"))
        if out == "open":
            out = _position_status(p, bid)   # also catch the live forming bar
        p["status"] = out

    await _reconcile_user_positions(symbol, resolution, norm)

    open_positions = [p for p in norm if p["status"] == "open"]
    active = max(open_positions, key=lambda p: p.get("entry_time_ms") or 0) if open_positions else None

    key = f"mt5:user_trade:{symbol}:{resolution}"
    if active:
        await r.set(key, json.dumps({
            "side":   active["side"],   "entry":  active["entry"],
            "stop":   active["stop"],   "target": active["target"],
            "entry_time_ms": active.get("entry_time_ms"),
        }), ex=86400)
        asyncio.create_task(_analyze_single(symbol, resolution))
    else:
        await r.delete(key)

    return {"ok": True, "persisted": len(norm),
            "open_count": len(open_positions), "active": active}
