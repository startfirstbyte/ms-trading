"""
Shared mutable application state — the pools/clients/manager that `lifespan`
populates at startup. Modules MUST import this module and read `state.X` at call
time (e.g. `state.redis_pool`), NEVER `from backend.core.state import redis_pool`
(that would bind the `None` placeholder before lifespan runs).
"""
from __future__ import annotations

import redis.asyncio as aioredis

from backend.core import config
from backend.core.ws_manager import ConnectionManager

redis_pool: aioredis.ConnectionPool | None = None
pg_pool: "config.asyncpg.Pool | None" = None

manager = ConnectionManager()
