"""WebSocket endpoints — realtime bar stream + AI analysis push."""
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core import config, state

router = APIRouter()


@router.websocket("/ws/ai/{symbol}")
async def ws_ai_endpoint(ws: WebSocket, symbol: str):
    """WebSocket cho AI analysis — client nhận push mỗi khi worker gửi kết quả mới."""
    await state.manager.connect(ws, symbol, "__ai__")
    try:
        # Gửi ngay toàn bộ cached AI data cho symbol này khi client kết nối
        r = aioredis.Redis(connection_pool=state.redis_pool)
        for res in config._AI_RESOLUTIONS:
            cached = await r.get(f"ai_analysis:{symbol}:{res}")
            if cached:
                await ws.send_json(json.loads(cached))

        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        state.manager.disconnect(ws, symbol, "__ai__")


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
