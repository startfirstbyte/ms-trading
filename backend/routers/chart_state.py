"""Chart-state endpoints — persist/restore TradingView drawings & layout (NOT AI)."""
import json

from fastapi import APIRouter, Body

from backend.core import state

router = APIRouter()


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
