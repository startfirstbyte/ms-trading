"""Hourly background job: resolve pending ai_predictions outcome → UPDATE result column."""
import asyncio
import logging

from backend.core import state
from backend.positions import _position_outcome

log = logging.getLogger(__name__)

_BATCH = 200   # max rows resolved per run


async def _resolve_pending_outcomes() -> None:
    if not state.pg_pool:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, symbol, resolution, signal, entry_zone, created_at, stop_loss, target
                FROM ai_predictions
                WHERE result IS NULL
                  AND signal IN ('BUY','BUY_LIMIT','BUY_STOP','SELL','SELL_LIMIT','SELL_STOP')
                  AND target    IS NOT NULL
                  AND stop_loss IS NOT NULL
                ORDER BY created_at DESC
                LIMIT $1
            """, _BATCH)
    except Exception as e:
        log.warning(f"outcome_resolver fetch failed: {e}")
        return

    if not rows:
        return

    resolved = 0
    for row in rows:
        entry_ms = int(row["created_at"].timestamp() * 1000)
        # For limit/stop signals, pass entry_zone for trigger-before-TP/SL check
        limit_zone = row["entry_zone"] if row["signal"] in ("BUY_LIMIT","BUY_STOP","SELL_LIMIT","SELL_STOP") else None
        outcome = await _position_outcome(
            row["symbol"], row["resolution"], row["signal"],
            entry_ms, row["stop_loss"], row["target"],
            entry_zone=limit_zone,
        )
        if outcome in ("open", "not_triggered"):
            continue   # còn pending hoặc limit chưa fill — để job sau check lại
        try:
            async with state.pg_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE ai_predictions SET result=$1 WHERE id=$2",
                    outcome, row["id"],
                )
            resolved += 1
        except Exception as e:
            log.warning(f"outcome_resolver update id={row['id']} failed: {e}")

    if resolved:
        log.info(f"outcome_resolver: resolved {resolved}/{len(rows)} predictions")


async def _outcome_resolver_loop() -> None:
    await asyncio.sleep(30)   # chờ server warmup
    log.info("Outcome resolver loop started (interval=1h)")
    while True:
        try:
            await _resolve_pending_outcomes()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"outcome_resolver loop error: {e}")
        await asyncio.sleep(3600)
