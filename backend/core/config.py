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
    import anthropic as _anthropic_lib
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _anthropic_lib = None  # type: ignore[assignment]
    _ANTHROPIC_AVAILABLE = False

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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL          = os.environ.get("AI_MODEL", "claude-sonnet-4-6")
# Nguồn gọi Claude: "api" = Anthropic API trả tiền theo token (mặc định, hành vi cũ);
# "local" = gọi sang Claude CLI Bridge trên host để dùng quota subscription phẳng.
AI_BACKEND        = os.environ.get("AI_BACKEND", "api").lower()
LOCAL_CLAUDE_URL  = os.environ.get("LOCAL_CLAUDE_URL", "http://host.containers.internal:8088")
AI_LOCAL_MODEL    = os.environ.get("AI_LOCAL_MODEL", "sonnet")   # alias model cho CLI bridge
# Trần số call Claude chạy SONG SONG (semaphore in-process). Ở "local" mỗi call spawn
# claude.exe (~431MB) nên để thấp để bound RAM host + ≤ BRIDGE_CONCURRENCY; ở "api" cho
# cao hơn vì rẻ RAM, bottleneck là độ trễ/rate-limit. Override qua env AI_MAX_CONCURRENCY.
AI_MAX_CONCURRENCY = int(os.environ.get("AI_MAX_CONCURRENCY", "3" if AI_BACKEND == "local" else "6"))
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

_AI_RESOLUTIONS = ["1", "3", "5", "15", "60"]
_AI_TF_NAMES    = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "60": "1H"}

# Cache TTLs (shared by analyzer + routers — kept here to avoid router↔service cycles)
_MS_TTL = 300    # 5-minute Market Structure cache
_AI_TTL = 3600   # 1-hour AI analysis cache

# Kill-switch cho vòng lặp auto-analysis (monitor). Redis key, value "1"/"0";
# vắng mặt = bật. Chỉ ảnh hưởng auto-trigger, KHÔNG ảnh hưởng nút Analyze thủ công.
AI_AUTO_FLAG_KEY = "ai:auto_enabled"

# Per-timeframe auto-trigger control. Key = f"{prefix}:{symbol}:{res}", value "0" = disabled.
# Absence = enabled (mặc định bật). Không ảnh hưởng nút Analyze thủ công.
AI_TF_ENABLED_PREFIX = "ai:tf_enabled"

TIMEFRAME_MAP: dict[str, int] = (
    {"1": mt5.TIMEFRAME_M1, "3": mt5.TIMEFRAME_M3, "5": mt5.TIMEFRAME_M5, "15": mt5.TIMEFRAME_M15, "60": mt5.TIMEFRAME_H1}
    if _MT5 else {}
)
