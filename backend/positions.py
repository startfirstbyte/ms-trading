"""User-position services — status/outcome evaluation + DB reconcile of locked trades."""
import logging

from backend.core import config, state

log = logging.getLogger(__name__)

# ── Signal type constants (shared with analyzer, outcome_resolver, winrate) ──
_BUY_SIGNALS      = {"BUY", "BUY_LIMIT", "BUY_STOP"}
_SELL_SIGNALS     = {"SELL", "SELL_LIMIT", "SELL_STOP"}
_ALL_DIRECTIONAL  = _BUY_SIGNALS | _SELL_SIGNALS
_VALID_SIGNALS    = _ALL_DIRECTIONAL | {"WAIT"}
# Limit/stop signals need a trigger check before TP/SL evaluation
_TRIGGER_RISE = {"BUY_STOP", "SELL_LIMIT"}    # price must RISE to entry_zone to fill
_TRIGGER_FALL = {"BUY_LIMIT", "SELL_STOP"}    # price must FALL to entry_zone to fill
_LIMIT_SIGNALS = _TRIGGER_RISE | _TRIGGER_FALL


def _sig_side(sig: str | None) -> str | None:
    """Return 'BUY', 'SELL', or None for WAIT/unknown."""
    if sig in _BUY_SIGNALS:  return "BUY"
    if sig in _SELL_SIGNALS: return "SELL"
    return None


def _position_status(p: dict, bid: float | None) -> str:
    """open | tp | sl — based on the LIVE price only (current forming bar)."""
    tp, sl, side = p.get("target"), p.get("stop"), p["side"]
    if bid is None or tp is None or sl is None:
        return "open"
    if side == "BUY":
        if bid >= tp: return "tp"
        if bid <= sl: return "sl"
    else:  # SELL
        if bid <= tp: return "tp"
        if bid >= sl: return "sl"
    return "open"


async def _position_outcome(symbol: str, resolution: str, signal: str | None,
                            entry_time_ms: int | None, stop: float | None,
                            target: float | None,
                            entry_zone: float | None = None) -> str:
    """open | tp | sl | not_triggered

    Uses 1m OHLC bars to check whether TP/SL has been touched (wicks count).
    For limit/stop signals (BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP):
      first checks if price reached entry_zone; if not → 'not_triggered'.
      If triggered, checks TP/SL from the trigger bar onward.
    'not_triggered' is treated as pending (neutral) in winrate/resolver.
    """
    if not state.pg_pool or target is None or stop is None or entry_time_ms is None:
        return "open"
    side = _sig_side(signal)
    if side is None:
        return "open"

    table = config._BAR_TABLE['1']
    check_from = entry_time_ms

    try:
        async with state.pg_pool.acquire() as conn:
            # Limit/stop: check whether price reached entry_zone first
            if signal in _TRIGGER_RISE and entry_zone is not None:
                trig = await conn.fetchrow(
                    f"SELECT MIN(time_ms) AS t FROM {table} WHERE symbol=$1 AND time_ms>=$2 AND high>=$3",
                    symbol, entry_time_ms, entry_zone)
                if trig["t"] is None:
                    return "not_triggered"
                check_from = trig["t"]
            elif signal in _TRIGGER_FALL and entry_zone is not None:
                trig = await conn.fetchrow(
                    f"SELECT MIN(time_ms) AS t FROM {table} WHERE symbol=$1 AND time_ms>=$2 AND low<=$3",
                    symbol, entry_time_ms, entry_zone)
                if trig["t"] is None:
                    return "not_triggered"
                check_from = trig["t"]

            if side == "BUY":   # TP above (high), SL below (low)
                row = await conn.fetchrow(
                    f"SELECT (SELECT MIN(time_ms) FROM {table} WHERE symbol=$1 AND time_ms>=$2 AND high>=$3) AS tp_t,"
                    f"       (SELECT MIN(time_ms) FROM {table} WHERE symbol=$1 AND time_ms>=$2 AND low<=$4)  AS sl_t",
                    symbol, check_from, target, stop)
            else:               # SELL side: TP below (low), SL above (high)
                row = await conn.fetchrow(
                    f"SELECT (SELECT MIN(time_ms) FROM {table} WHERE symbol=$1 AND time_ms>=$2 AND low<=$3)  AS tp_t,"
                    f"       (SELECT MIN(time_ms) FROM {table} WHERE symbol=$1 AND time_ms>=$2 AND high>=$4) AS sl_t",
                    symbol, check_from, target, stop)
    except Exception:
        return "open"
    tp_t, sl_t = row["tp_t"], row["sl_t"]
    if tp_t is None and sl_t is None:
        return "open"
    if tp_t is not None and (sl_t is None or tp_t <= sl_t):
        return "tp"
    return "sl"


async def _reconcile_user_positions(symbol: str, resolution: str, positions: list[dict]) -> None:
    """Make user_position rows for (symbol, resolution) MIRROR the currently
    locked set on the chart: upsert the present ones, and DELETE any that are no
    longer locked/drawn (user unfroze or deleted the position)."""
    if not state.pg_pool:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            async with conn.transaction():
                for p in positions:
                    await conn.execute("""
                        INSERT INTO user_position
                            (symbol, resolution, shape_id, side, entry, stop, target, entry_time_ms, status, updated_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
                        ON CONFLICT (symbol, resolution, shape_id)
                        DO UPDATE SET side=EXCLUDED.side, entry=EXCLUDED.entry, stop=EXCLUDED.stop,
                                      target=EXCLUDED.target, entry_time_ms=EXCLUDED.entry_time_ms,
                                      status=EXCLUDED.status, updated_at=NOW()
                    """, symbol, resolution, p["shape_id"], p["side"], p["entry"], p.get("stop"),
                         p.get("target"), p.get("entry_time_ms"), p.get("status", "open"))
                # Drop rows whose shape is no longer present (unfrozen/deleted).
                # Empty array → `<> ALL('{}')` is TRUE for every row → clears all.
                ids = [p["shape_id"] for p in positions]
                await conn.execute(
                    "DELETE FROM user_position WHERE symbol=$1 AND resolution=$2 "
                    "AND shape_id <> ALL($3::text[])",
                    symbol, resolution, ids,
                )
    except Exception as e:
        log.warning(f"user_position reconcile failed: {e}")
