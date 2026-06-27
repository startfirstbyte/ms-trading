"""
Shared mutable application state — the pools/clients/manager that `lifespan`
populates at startup. Modules MUST import this module and read `state.X` at call
time (e.g. `state.redis_pool`), NEVER `from backend.core.state import redis_pool`
(that would bind the `None` placeholder before lifespan runs).
"""
from __future__ import annotations

import asyncio

import redis.asyncio as aioredis

from backend.core import config
from backend.core.ws_manager import ConnectionManager

redis_pool: aioredis.ConnectionPool | None = None
pg_pool: "config.asyncpg.Pool | None" = None
ai_client: "config._anthropic_lib.AsyncAnthropic | None" = None

# Giới hạn concurrency cho các call Claude (Option 1). Khởi tạo trong lifespan
# (Semaphore phải bind vào event loop đang chạy). None = chưa init → coi như không giới hạn.
ai_semaphore: asyncio.Semaphore | None = None
# Coalescing/dedup (Option 2): (symbol, resolution) → Task đang phân tích, để các
# trigger force=False trùng frame DÙNG CHUNG một call Claude thay vì gọi lặp.
ai_inflight: dict[tuple[str, str], "asyncio.Task"] = {}

manager = ConnectionManager()
