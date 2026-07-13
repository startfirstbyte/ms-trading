"""
Central configuration: env vars, constants, optional-library guards, logging.
Import this module everywhere (`from backend.core import config`) and read
`config.X` so there is a single source of truth and no scattered os.environ calls.
"""
import logging
import os

from dotenv import load_dotenv

# ── Optional libraries (graceful degradation if not installed) ──────────────────
try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
    asyncpg = None  # type: ignore[assignment]
    _ASYNCPG_AVAILABLE = False

try:
    import MetaTrader5 as mt5
    _MT5 = True
except ImportError:
    mt5 = None  # type: ignore[assignment]
    _MT5 = False

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [server] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)

# ── Env-backed config ───────────────────────────────────────────────────────────
SYMBOLS           = ["XAUUSD", "BTCUSD", "USOIL"]
WEBHOOK_SECRET    = os.environ.get("WEBHOOK_SECRET", "dev-secret")
REDIS_URL         = os.environ.get("REDIS_URL", "redis://localhost:6379")
DATABASE_URL      = os.environ.get("DATABASE_URL", "")
TTL_SECONDS       = 30 * 24 * 3600
CUTOFF_MS         = TTL_SECONDS * 1000

_BAR_TABLE = {
    '1': 'bars_1m', '3': 'bars_3m', '5': 'bars_5m',
    '15': 'bars_15m', '60': 'bars_60m'
}
_PG_RETENTION_DAYS = {
    '1': 30, '3': 45, '5': 60, '15': 90, '60': 90
}

# Cache TTL — Market Structure cache (read by market_structure router).
_MS_TTL = 300    # 5-minute Market Structure cache

TIMEFRAME_MAP: dict[str, int] = (
    {"1": mt5.TIMEFRAME_M1, "3": mt5.TIMEFRAME_M3, "5": mt5.TIMEFRAME_M5, "15": mt5.TIMEFRAME_M15, "60": mt5.TIMEFRAME_H1}
    if _MT5 else {}
)
