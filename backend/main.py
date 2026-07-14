"""
FastAPI app factory + lifespan — chạy trên Cloud.
Nhận dữ liệu từ worker qua webhook → lưu Redis → push xuống client qua WebSocket.

Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core import config, state
from backend.db.postgres import _init_pg_schema, _pg_cleanup_loop
from backend.db.realtime import _pubsub_listener
from backend.routers import chart_state, history, market_structure, webhooks, websocket

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.redis_pool = aioredis.ConnectionPool.from_url(
        config.REDIS_URL, decode_responses=True, max_connections=20,
        health_check_interval=30, socket_keepalive=True,
    )
    log.info(f"Redis pool → {config.REDIS_URL}")

    # PostgreSQL
    if config.DATABASE_URL and config._ASYNCPG_AVAILABLE:
        state.pg_pool = await config.asyncpg.create_pool(config.DATABASE_URL, min_size=2, max_size=10)
        await _init_pg_schema(state.pg_pool)
        log.info(f"PostgreSQL pool ready → {config.DATABASE_URL.split('@')[-1]}")
    else:
        log.warning("DATABASE_URL not set or asyncpg unavailable — bars will not be persisted to PostgreSQL")

    if not config._MT5:
        log.warning("MT5 not installed — resolve/symbols use static config; history fallback disabled")
    elif not config.mt5.initialize():
        log.warning("MT5 not available — resolve/symbols endpoints will fail")
    else:
        login = os.environ.get("MT5_LOGIN")
        if login:
            config.mt5.login(int(login), password=os.environ["MT5_PASSWORD"], server=os.environ["MT5_SERVER"])
        log.info(f"MT5 ready: {config.mt5.terminal_info().name}")

    listener = asyncio.create_task(_pubsub_listener())
    cleanup  = asyncio.create_task(_pg_cleanup_loop())

    yield

    listener.cancel()
    cleanup.cancel()
    if config._MT5:
        config.mt5.shutdown()
    if state.pg_pool:
        await state.pg_pool.close()
    await state.redis_pool.aclose()


app = FastAPI(title="MT5 Chart API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for _router in (
    websocket.router,
    webhooks.router,
    history.router,
    market_structure.router,
    chart_state.router,
):
    app.include_router(_router)
