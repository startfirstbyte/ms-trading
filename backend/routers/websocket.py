"""WebSocket endpoints — realtime bar stream."""
import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core import state

router = APIRouter()


@router.websocket("/ws/{symbol}/{resolution}")
async def ws_endpoint(ws: WebSocket, symbol: str, resolution: str):
    await state.manager.connect(ws, symbol, resolution)
    try:
        r = aioredis.Redis(connection_pool=state.redis_pool)
        live = await r.hgetall(f"mt5:live:{symbol}:{resolution}")
        if live:
            bar = {k: int(float(v)) if k in ("volume", "time") else float(v) for k, v in live.items()}
            await ws.send_json(bar)

        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        state.manager.disconnect(ws, symbol, resolution)
