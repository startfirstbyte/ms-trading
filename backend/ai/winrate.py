"""Recent BUY/SELL win-rate (TP-first vs SL-first), cached, for prompt feedback."""
import json

import redis.asyncio as aioredis

from backend.core import config, state
from backend.positions import _position_outcome


async def _recent_winrate(symbol: str, resolution: str, limit: int = 20) -> dict | None:
    """Score recent BUY/SELL predictions using stored result column (fast path).
    Falls back to live _position_outcome() only for rows still pending (result IS NULL).
    Cached 10 min in Redis."""
    if not state.pg_pool:
        return None
    r = aioredis.Redis(connection_pool=state.redis_pool)
    cache_key = f"winrate:{symbol}:{resolution}"
    cached = await r.get(cache_key)
    if cached:
        try: return json.loads(cached)
        except Exception: pass
    try:
        async with state.pg_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT signal, entry_zone, target, stop_loss, created_at, result
                FROM ai_predictions
                WHERE symbol=$1 AND resolution=$2
                  AND signal IN ('BUY','BUY_LIMIT','BUY_STOP','SELL','SELL_LIMIT','SELL_STOP')
                  AND target IS NOT NULL AND stop_loss IS NOT NULL
                ORDER BY created_at DESC LIMIT $3
            """, symbol, resolution, limit)
    except Exception:
        return None
    wins = losses = pending = 0
    for row in rows:
        outcome = row["result"]
        if outcome is None:
            entry_ms  = int(row["created_at"].timestamp() * 1000)
            limit_zone = row["entry_zone"] if row["signal"] in ("BUY_LIMIT","BUY_STOP","SELL_LIMIT","SELL_STOP") else None
            outcome = await _position_outcome(symbol, resolution, row["signal"],
                                              entry_ms, row["stop_loss"], row["target"],
                                              entry_zone=limit_zone)
        if   outcome == "tp": wins    += 1
        elif outcome == "sl": losses  += 1
        else:                 pending += 1   # open or not_triggered → pending
    decided = wins + losses
    result = {"wins": wins, "losses": losses, "pending": pending,
              "decided": decided, "rate": (wins / decided) if decided else None}
    await r.set(cache_key, json.dumps(result), ex=600)
    return result


async def _losing_trades_stats(symbol: str, per_tf_limit: int = 40, loser_limit: int = 25) -> dict:
    """Per-timeframe BUY/SELL outcome stats + losing trade list.
    Uses stored result column; falls back to live compute for still-pending rows.
    Cached 5 min per symbol."""
    empty = {"symbol": symbol, "timeframes": [], "total_losses": 0}
    if not state.pg_pool:
        return empty
    r = aioredis.Redis(connection_pool=state.redis_pool)
    cache_key = f"losing_stats:{symbol}"
    cached = await r.get(cache_key)
    if cached:
        try: return json.loads(cached)
        except Exception: pass

    timeframes = []
    total_losses = 0
    for res in config._AI_RESOLUTIONS:
        try:
            async with state.pg_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, signal, conviction, entry_zone, target, stop_loss,
                           created_at, context, result
                    FROM ai_predictions
                    WHERE symbol=$1 AND resolution=$2
                      AND signal IN ('BUY','BUY_LIMIT','BUY_STOP','SELL','SELL_LIMIT','SELL_STOP')
                      AND target IS NOT NULL AND stop_loss IS NOT NULL
                    ORDER BY created_at DESC LIMIT $3
                """, symbol, res, per_tf_limit)
        except Exception:
            rows = []

        wins = losses = pending = 0
        losers: list[dict] = []
        for row in rows:
            outcome = row["result"]
            if outcome is None:
                entry_ms   = int(row["created_at"].timestamp() * 1000)
                limit_zone = row["entry_zone"] if row["signal"] in ("BUY_LIMIT","BUY_STOP","SELL_LIMIT","SELL_STOP") else None
                outcome = await _position_outcome(symbol, res, row["signal"],
                                                  entry_ms, row["stop_loss"], row["target"],
                                                  entry_zone=limit_zone)
            if outcome == "tp":
                wins += 1
            elif outcome == "sl":
                losses += 1
                if len(losers) < loser_limit:
                    entry, sl = row["entry_zone"], row["stop_loss"]
                    loss_pct = round(abs(entry - sl) / entry * 100, 2) if entry else None
                    regime = None
                    ctx = row["context"]
                    if ctx:
                        try:
                            c = ctx if isinstance(ctx, dict) else json.loads(ctx)
                            regime = (c.get("regime") or {}).get("regime")
                        except Exception:
                            pass
                    losers.append({
                        "id":         row["id"],
                        "created_at": row["created_at"].isoformat(),
                        "signal":     row["signal"],
                        "conviction": row["conviction"],
                        "entry":      entry,
                        "target":     row["target"],
                        "stop_loss":  sl,
                        "loss_pct":   loss_pct,
                        "regime":     regime,
                    })
            else:
                pending += 1

        decided = wins + losses
        total_losses += losses
        timeframes.append({
            "resolution": res, "wins": wins, "losses": losses, "pending": pending,
            "decided": decided, "rate": (wins / decided) if decided else None,
            "losers": losers,
        })

    result = {"symbol": symbol, "timeframes": timeframes, "total_losses": total_losses}
    await r.set(cache_key, json.dumps(result), ex=300)
    return result
