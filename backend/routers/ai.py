"""AI analysis endpoints — cached read, history, winrate, chart-state, on-demand analyze."""
import asyncio
import json
import logging
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Body, HTTPException, Query

from backend.core import config, state
from backend.ai.analyzer import analyze_coalesced, analyze_tiered, _analyze_single, _fetch_htf_context
from backend.ai.winrate import _recent_winrate, _losing_trades_stats

router = APIRouter()
log = logging.getLogger(__name__)

_EMPTY_AI = {
    "signal": "WAIT", "conviction": "LOW", "win_pct": None,
    "trigger": None, "watch_buy": None, "watch_sell": None,
    "key_level": None, "est_bars": None,
    "analysis": "No AI analysis yet.", "entry_zone": None,
    "target": None, "stop_loss": None, "token_stats": {},
    "ms_pattern": "none", "ms_confidence": 0.0, "timestamp_ms": 0,
}


@router.get("/api/ai_analysis")
async def get_ai_analysis(symbol: str, resolution: str):
    r = aioredis.Redis(connection_pool=state.redis_pool)
    cached = await r.get(f"ai_analysis:{symbol}:{resolution}")
    if cached:
        return json.loads(cached)
    return {**_EMPTY_AI, "symbol": symbol, "resolution": resolution}


@router.get("/api/ai_history")
async def get_ai_history(
    symbol: str,
    resolution: str,
    limit: int = Query(default=20, le=100),
):
    """Return recent AI prediction history from PostgreSQL."""
    if not state.pg_pool:
        raise HTTPException(503, "PostgreSQL not available")
    async with state.pg_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT signal, conviction, trigger, analysis,
                   entry_zone, target, stop_loss, key_level,
                   analysis_bid, prediction_updated, update_reason,
                   trade_status, trade_note, trigger_event,
                   created_at
            FROM ai_predictions
            WHERE symbol=$1 AND resolution=$2
            ORDER BY created_at DESC
            LIMIT $3
        """, symbol, resolution, limit)
    return [dict(r) for r in rows]


@router.get("/api/winrate")
async def get_winrate(symbol: str, resolution: str):
    """Recent BUY/SELL win-rate (TP-first vs SL-first) for a timeframe."""
    wr = await _recent_winrate(symbol, resolution)
    return wr or {"wins": 0, "losses": 0, "pending": 0, "decided": 0, "rate": None}


@router.get("/api/losing_trades")
async def get_losing_trades(symbol: str):
    """Per-timeframe BUY/SELL stats + list of losing trades (SL hit before TP)."""
    return await _losing_trades_stats(symbol)


@router.post("/api/ai/reset")
async def ai_reset(symbol: str = Query(...), resolution: str = Query(...)):
    """Unlock/reset the locked signal for ONE timeframe — force a fresh analysis
    (bypasses the active-signal lock) and overwrite the cached signal."""
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"Unknown symbol {symbol!r}")
    result = await _analyze_single(symbol, resolution, trigger_event="reset")
    return {"ok": result is not None, "result": result}


@router.get("/api/ai/auto")
async def get_ai_auto():
    """Trạng thái kill-switch auto-analysis (server-wide). enabled=false → server
    ngừng tự gọi Claude; nút Analyze thủ công không bị ảnh hưởng."""
    r = aioredis.Redis(connection_pool=state.redis_pool)
    flag = await r.get(config.AI_AUTO_FLAG_KEY)
    return {"enabled": flag != "0"}   # vắng mặt = bật


@router.post("/api/ai/auto")
async def set_ai_auto(enabled: bool = Query(...)):
    """Bật/tắt auto-analysis server-wide để tiết kiệm chi phí API."""
    r = aioredis.Redis(connection_pool=state.redis_pool)
    await r.set(config.AI_AUTO_FLAG_KEY, "1" if enabled else "0")
    log.info(f"AI auto-analysis {'ENABLED' if enabled else 'DISABLED'} via API")
    return {"enabled": enabled}


@router.get("/api/ai/tf_config")
async def get_tf_config(symbol: str = Query(...)):
    """Trả về {res: enabled} cho tất cả 5 TF của symbol."""
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"Unknown symbol {symbol!r}")
    r = aioredis.Redis(connection_pool=state.redis_pool)
    result: dict[str, bool] = {}
    for res in config._AI_RESOLUTIONS:
        flag = await r.get(f"{config.AI_TF_ENABLED_PREFIX}:{symbol}:{res}")
        result[res] = flag != "0"
    return result


@router.post("/api/ai/tf_enabled")
async def set_tf_enabled(
    symbol: str = Query(...),
    resolution: str = Query(...),
    enabled: bool = Query(...),
):
    """Bật/tắt auto-trigger cho 1 timeframe. Không ảnh hưởng nút Analyze thủ công."""
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"Unknown symbol {symbol!r}")
    if resolution not in config._AI_RESOLUTIONS:
        raise HTTPException(400, f"Unknown resolution {resolution!r}")
    r = aioredis.Redis(connection_pool=state.redis_pool)
    await r.set(f"{config.AI_TF_ENABLED_PREFIX}:{symbol}:{resolution}", "1" if enabled else "0")
    log.info(f"AI auto-trigger [{symbol}:{resolution}] {'ENABLED' if enabled else 'DISABLED'}")
    return {"symbol": symbol, "resolution": resolution, "enabled": enabled}


@router.get("/api/chart_state")
async def get_chart_state(symbol: str, layout_id: str = "default"):
    """Load saved TradingView chart state (drawings, indicators) for a symbol."""
    if not state.pg_pool:
        return None
    async with state.pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state FROM chart_states WHERE symbol=$1 AND layout_id=$2",
            symbol, layout_id,
        )
    return row["state"] if row else None


@router.post("/api/chart_state")
async def save_chart_state(symbol: str, layout_id: str = "default", state_body: dict = Body(..., embed=False)):
    """Save TradingView chart state (called on every auto-save event)."""
    if not state.pg_pool or state_body is None:
        return {"ok": False}
    async with state.pg_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO chart_states (symbol, layout_id, state, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (symbol, layout_id) DO UPDATE
                SET state=EXCLUDED.state, updated_at=NOW()
        """, symbol, layout_id, json.dumps(state_body))
    return {"ok": True}


@router.post("/api/ai/analyze")
async def ai_analyze(
    symbol: str = Query(...),
    force: bool = Query(False),
    resolution: str | None = Query(None),
):
    """Analyze timeframes for a symbol.
    - force=false (default): skip if same EW pattern + price within 0.5% + analysis < 5min old
    - force=true: always call Claude (user explicitly wants fresh reading)
    - resolution: nếu có → CHỈ phân tích đúng khung đó (zone-hit/per-TF, tiết kiệm chi phí);
      None → fan-out cả 5 khung (nút Analyze).
    """
    if config.AI_BACKEND != "local" and not state.ai_client:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured — set it in .env and rebuild")

    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"Unknown symbol {symbol!r}")

    if resolution is not None and resolution not in config._AI_RESOLUTIONS:
        raise HTTPException(400, f"Unknown resolution {resolution!r}")
    resolutions = [resolution] if resolution else config._AI_RESOLUTIONS

    t0 = time.time()
    r  = aioredis.Redis(connection_pool=state.redis_pool)

    quote_raw = await r.hgetall(f"mt5:quote:{symbol}")
    quote = {k: float(v) for k, v in quote_raw.items()} if quote_raw else None

    htf_context = await _fetch_htf_context(r, symbol)

    # Top-down phân tầng: 60→15 tuần tự (thread kết quả tươi) → khung nhỏ song song.
    # Thay cho gather(5) đọc cache HTF cũ — khung nhỏ giờ thấy HTF của CHÍNH lượt này.
    results_map = await analyze_tiered(r, symbol, resolutions, quote,
                                       force=force, htf_context=htf_context)

    elapsed = round(time.time() - t0, 1)
    out: dict[str, dict] = {}
    for res in resolutions:
        result = results_map.get(res)
        if result is None or isinstance(result, Exception):
            log.error(f"AI [{symbol}:{res}] failed: {result}")
            out[res] = {**_EMPTY_AI, "symbol": symbol, "resolution": res,
                        "analysis": str(result)[:120] if result else "no result",
                        "timestamp_ms": int(time.time() * 1000)}
        else:
            out[res] = result

    log.info(f"AI analyze {symbol} [{','.join(resolutions)}] in {elapsed}s")
    return {"ok": True, "symbol": symbol, "elapsed_s": elapsed, "results": out}
