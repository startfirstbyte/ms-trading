"""HTTP datafeed endpoints — config, symbols, resolve, history, quote."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Query

from backend.core import config, state
from backend.db.postgres import _query_bars_pg, _query_bars_pg_before

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/api/config")
def get_config():
    return {
        "supported_resolutions": ["1", "3", "5", "15", "60"],
        "supports_search":        True,
        "supports_group_request": False,
        "supports_marks":         False,
        "supports_timescale_marks": False,
    }


_STATIC_SYMBOLS = [
    {"name": "XAUUSD", "description": "Gold vs US Dollar"},
    {"name": "BTCUSD", "description": "Bitcoin vs US Dollar"},
    {"name": "USOIL",  "description": "Crude Oil (WTI)"},
]

_STATIC_SYMBOL_INFO: dict[str, dict] = {
    "XAUUSD": {"name": "XAUUSD", "description": "Gold vs US Dollar",   "pricescale": 100,    "type": "forex"},
    "BTCUSD": {"name": "BTCUSD", "description": "Bitcoin vs US Dollar", "pricescale": 100,    "type": "crypto"},
    "USOIL":  {"name": "USOIL",  "description": "Crude Oil (WTI)",      "pricescale": 100,    "type": "futures"},
}

# Session per-symbol. The broker streams XAUUSD/USOIL on weekends too (live bars
# appear Saturday), so all symbols use "24x7" — TradingView plots every bar the
# feed returns instead of hiding weekend days.
# NOTE: TradingView day numbering is 1=Sun, 2=Mon … 7=Sat. The old default
# "0000-2400:12345" meant Sun–Thu (it hid Fri AND Sat), not Mon–Fri.
_SYMBOL_SESSION: dict[str, str] = {
    "BTCUSD": "24x7",
    "XAUUSD": "24x7",
    "USOIL":  "24x7",
}
_SESSION_DEFAULT = "24x7"

_SYMBOL_INFO_BASE = {
    "minmov": 1, "timezone": "Etc/UTC",
    "exchange": "MT5", "has_intraday": True,
    "supported_resolutions": ["1", "3", "5", "15", "60"],
    "volume_precision": 0, "data_status": "streaming",
}


@router.get("/api/symbols")
def get_symbols(query: str = ""):
    if not config._MT5:
        q = query.lower()
        return [s for s in _STATIC_SYMBOLS if not q or q in s["name"].lower() or q in s["description"].lower()]
    syms = config.mt5.symbols_get(f"*{query}*") if query else config.mt5.symbols_get()
    if not syms:
        return []
    return [{"name": s.name, "description": s.description} for s in syms[:50]]


@router.get("/api/resolve")
def resolve_symbol(symbol: str):
    sym_upper = symbol.upper()
    session   = _SYMBOL_SESSION.get(sym_upper, _SESSION_DEFAULT)
    if not config._MT5:
        static = _STATIC_SYMBOL_INFO.get(sym_upper)
        if not static:
            raise HTTPException(404, f"Symbol {symbol!r} not found")
        return {**_SYMBOL_INFO_BASE, "session": session, **static}
    info = config.mt5.symbol_info(symbol)
    if info is None:
        raise HTTPException(404, f"Symbol {symbol!r} not found")
    return {
        **_SYMBOL_INFO_BASE,
        "session":     session,
        "name":        info.name,
        "description": info.description,
        "pricescale":  int(round(1 / info.point)),
        "type":        "forex",
    }


@router.get("/api/history")
async def get_history(
    symbol:     str,
    resolution: str,
    from_time:  int = Query(..., alias="from"),
    to_time:    int = Query(..., alias="to"),
):
    from_ms = from_time * 1000
    to_ms   = to_time   * 1000

    r = aioredis.Redis(connection_pool=state.redis_pool)

    # 1. Redis hot cache — closed bars từ sorted set
    raw  = await r.zrangebyscore(f"mt5:bars:{symbol}:{resolution}", from_ms, to_ms)
    bars = [json.loads(b) for b in raw]

    # 2. PostgreSQL fallback nếu Redis miss
    if not bars and state.pg_pool:
        bars = await _query_bars_pg(symbol, resolution, from_ms, to_ms)
        if bars:
            log.info(f"PG fallback {symbol}:{resolution} got {len(bars)} bars — backfilling Redis")
            # Backfill Redis async (không block response)
            async def _backfill(bars_copy: list[dict] = bars, res_copy: str = resolution) -> None:
                pipe2 = r.pipeline(transaction=False)
                for b in bars_copy:
                    pipe2.zadd(f"mt5:bars:{symbol}:{res_copy}",
                               {json.dumps(b): b['time']}, nx=True)
                pipe2.expire(f"mt5:bars:{symbol}:{res_copy}", config.TTL_SECONDS)
                await pipe2.execute()
            asyncio.create_task(_backfill())

    now_ms = int(time.time() * 1000)
    is_current_request = to_ms >= now_ms - 120_000   # to_ms trong vòng 2 phút gần nhất

    # 2b. Cửa sổ [from,to] rỗng → trả các bar gần nhất TRƯỚC to_ms thay vì noData.
    #     Nếu chỉ trả noData, TradingView coi như "hết dữ liệu" và NGỪNG phân trang lùi.
    #     Hai trường hợp đều cần xử lý:
    #       - firstDataRequest khi thị trường đóng (cuối tuần) → vẽ phiên gần nhất.
    #       - kéo lùi rơi trúng GAP dữ liệu (vd gap cuối tuần 53h) → "nhảy" qua gap để
    #         lịch sử cũ hơn phía sau gap tiếp tục load từ PG (infinite scroll).
    #     Chỉ khi không còn bất kỳ bar nào cũ hơn (PG cạn) mới trả noData = hết thật.
    if not bars:
        recent_raw = await r.zrevrangebyscore(
            f"mt5:bars:{symbol}:{resolution}", to_ms, "-inf", start=0, num=500
        )
        bars = [json.loads(b) for b in recent_raw]
        if not bars and state.pg_pool:
            bars = await _query_bars_pg_before(symbol, resolution, to_ms, limit=500)
        if bars:
            log.info(f"Gap fallback {symbol}:{resolution} → {len(bars)} bar trước to_ms (cửa sổ rỗng)")

    # 3. Append live bar nếu trong range, hoặc nếu to_ms là "recent" (request hiện tại, không phải pagination)
    live = await r.hgetall(f"mt5:live:{symbol}:{resolution}")
    if live:
        live_bar = {k: int(float(v)) if k in ("volume", "time") else float(v) for k, v in live.items()}
        live_time = live_bar.get("time", 0)
        if live_time >= from_ms and (live_time <= to_ms or is_current_request):
            if not bars or bars[-1]["time"] != live_time:
                bars.append(live_bar)

    # 4. MT5 fallback cuối cùng (nếu cả Redis + PG đều miss)
    if not bars:
        log.warning(f"Redis+PG miss {symbol}:{resolution}, falling back to MT5")
        return _mt5_history(symbol, resolution, from_time, to_time)

    bars.sort(key=lambda x: x["time"])
    return {"bars": bars, "noData": False}


def _mt5_history(symbol: str, resolution: str, from_time: int, to_time: int) -> dict:
    if not config._MT5:
        return {"bars": [], "noData": True}
    tf = config.TIMEFRAME_MAP.get(resolution)
    if not tf:
        return {"bars": [], "noData": True}
    date_from = datetime.fromtimestamp(from_time, tz=timezone.utc)
    date_to   = datetime.fromtimestamp(to_time,   tz=timezone.utc)
    rates = config.mt5.copy_rates_range(symbol, tf, date_from, date_to)
    if rates is None or len(rates) == 0:
        return {"bars": [], "noData": True}
    return {
        "bars": [
            {"time": int(r["time"]) * 1000, "open": float(r["open"]),
             "high": float(r["high"]),      "low":  float(r["low"]),
             "close": float(r["close"]),    "volume": int(r["tick_volume"])}
            for r in rates
        ],
        "noData": False,
    }


@router.get("/api/quote")
async def get_quote(symbol: str):
    r = aioredis.Redis(connection_pool=state.redis_pool)
    data = await r.hgetall(f"mt5:quote:{symbol}")
    if data:
        return {"bid": float(data["bid"]), "ask": float(data["ask"]), "time": float(data["time"])}

    if not config._MT5:
        raise HTTPException(503, f"No quote cached for {symbol!r}")
    tick = config.mt5.symbol_info_tick(symbol)
    if tick is None:
        raise HTTPException(404, f"No tick for {symbol!r}")
    return {"bid": tick.bid, "ask": tick.ask, "time": tick.time * 1000}
