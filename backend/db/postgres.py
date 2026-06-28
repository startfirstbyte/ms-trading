"""PostgreSQL helpers — schema init, bar upsert/query, AI-prediction insert, cleanup."""
import asyncio
import json
import logging
import time

from backend.core import config, state

log = logging.getLogger(__name__)


async def _init_pg_schema(pool: "config.asyncpg.Pool") -> None:
    ddl_col = (
        "symbol TEXT NOT NULL, time_ms BIGINT NOT NULL, "
        "open DOUBLE PRECISION NOT NULL, high DOUBLE PRECISION NOT NULL, "
        "low DOUBLE PRECISION NOT NULL, close DOUBLE PRECISION NOT NULL, "
        "volume DOUBLE PRECISION NOT NULL DEFAULT 0, "
        "PRIMARY KEY (symbol, time_ms)"
    )
    async with pool.acquire() as conn:
        for table in config._BAR_TABLE.values():
            await conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({ddl_col})")
        # AI prediction history
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_predictions (
                id            BIGSERIAL PRIMARY KEY,
                symbol        TEXT NOT NULL,
                resolution    TEXT NOT NULL,
                signal        TEXT,
                conviction    TEXT,
                trigger       TEXT,
                analysis      TEXT,
                entry_zone    DOUBLE PRECISION,
                target        DOUBLE PRECISION,
                stop_loss     DOUBLE PRECISION,
                key_level     DOUBLE PRECISION,
                watch_buy     DOUBLE PRECISION,
                watch_sell    DOUBLE PRECISION,
                analysis_bid  DOUBLE PRECISION,
                prediction_updated BOOLEAN,
                update_reason TEXT,
                trade_status  TEXT,
                trade_note    TEXT,
                trigger_event TEXT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                result        TEXT
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ai_pred_sym_res_time "
            "ON ai_predictions(symbol, resolution, created_at DESC)"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_records (
                id          BIGSERIAL PRIMARY KEY,
                symbol      TEXT NOT NULL,
                resolution  TEXT NOT NULL,
                direction   TEXT NOT NULL,
                entry       DOUBLE PRECISION NOT NULL,
                stop_loss   DOUBLE PRECISION,
                target      DOUBLE PRECISION,
                note        TEXT,
                source      TEXT DEFAULT 'manual',
                result      TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS trade_rec_sym_time "
            "ON trade_records(symbol, created_at DESC)"
        )
        # Chart drawings state — one row per (symbol, layout_id)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chart_states (
                symbol      TEXT NOT NULL,
                layout_id   TEXT NOT NULL DEFAULT 'default',
                state       JSONB NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (symbol, layout_id)
            )
        """)
        # User-drawn positions that were LOCKED on the chart = real entered orders.
        # Identity = the TradingView shape EntityId (stable within a session) so
        # moving a position updates the same row and deleting it removes exactly it.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_position (
                id            BIGSERIAL PRIMARY KEY,
                symbol        TEXT NOT NULL,
                resolution    TEXT NOT NULL,
                shape_id      TEXT NOT NULL,                 -- TradingView EntityId
                side          TEXT NOT NULL,                 -- 'BUY' | 'SELL'
                entry         DOUBLE PRECISION NOT NULL,
                stop          DOUBLE PRECISION,
                target        DOUBLE PRECISION,
                entry_time_ms BIGINT,                        -- entry anchor time on chart
                status        TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'tp' | 'sl' | 'closed'
                locked_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        # Migrate older schema (keyed by side+entry_time_ms) → shape_id identity
        await conn.execute("ALTER TABLE user_position ADD COLUMN IF NOT EXISTS shape_id TEXT")
        await conn.execute("ALTER TABLE user_position ALTER COLUMN entry_time_ms DROP NOT NULL")
        await conn.execute(
            "ALTER TABLE user_position DROP CONSTRAINT IF EXISTS "
            "user_position_symbol_resolution_side_entry_time_ms_key"
        )
        await conn.execute("DELETE FROM user_position WHERE shape_id IS NULL")  # legacy rows re-sync from chart
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS user_pos_shape "
            "ON user_position(symbol, resolution, shape_id)"
        )
        # Backtest: full computation snapshot at decision time (MS/channel/regime/
        # momentum/bias) attached to each prediction so a run can be replayed.
        await conn.execute("ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS context JSONB")
        await conn.execute("ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS result TEXT")
        # Market Structure snapshots — historical detect() results for chart replay.
        # `compute_ms` (routers/market_structure.py) INSERTs here with
        # ON CONFLICT (symbol, resolution, computed_at) → needs the `ms_unique` constraint.
        # (DDL added to code 2026-06-21 — previously the table was created out-of-band.)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ms_snapshots (
                id          BIGSERIAL PRIMARY KEY,
                symbol      VARCHAR(20) NOT NULL,
                resolution  VARCHAR(5)  NOT NULL,
                computed_at BIGINT      NOT NULL,
                pattern     VARCHAR(60) NOT NULL,
                confidence  REAL        NOT NULL,
                waves       JSONB       NOT NULL DEFAULT '[]'::jsonb,
                channel     JSONB,
                structure   JSONB,
                CONSTRAINT ms_unique UNIQUE (symbol, resolution, computed_at)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ms_sym_res "
            "ON ms_snapshots(symbol, resolution, computed_at DESC)"
        )
        # ZigZag mịn riêng cho chart (tách khỏi `waves` cấu trúc dùng cho AI).
        await conn.execute("ALTER TABLE ms_snapshots ADD COLUMN IF NOT EXISTS draw_waves JSONB")
        # Price channels — persisted parallel channels with a 3-state lifecycle:
        #   status='editing'   → re-fit tự do mỗi nến; phá khi non → xoá làm lại (1/symbol:res).
        #   status='confirmed' → đủ pivot (≥4 chạm rail) → khoá slope, chỉ kéo dài; (1/symbol:res).
        #   status='committed' → giá phá biên + buffer → đóng băng, lịch sử.
        # editing & confirmed SỐNG SONG SONG (nesting: trend nhỏ trong trend lớn).
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS price_channels (
                id           BIGSERIAL PRIMARY KEY,
                symbol       TEXT NOT NULL,
                resolution   TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'editing',  -- 'editing' | 'confirmed' | 'committed'
                channel      JSONB NOT NULL,                   -- geometry + measurements (MSChannel shape)
                direction    TEXT,
                width        DOUBLE PRECISION,
                width_pct    DOUBLE PRECISION,
                time_start   BIGINT NOT NULL,
                time_end     BIGINT NOT NULL,
                break_side   TEXT,                             -- 'upper' | 'lower' (khi committed)
                break_price  DOUBLE PRECISION,
                break_time   BIGINT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                committed_at TIMESTAMPTZ,
                confirmed_at TIMESTAMPTZ
            )
        """)
        await conn.execute("ALTER TABLE price_channels ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ")
        # Mỗi (symbol, resolution): tối đa 1 editing VÀ 1 confirmed (hai index riêng → cùng tồn tại).
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS price_channels_one_editing "
            "ON price_channels(symbol, resolution) WHERE status='editing'"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS price_channels_one_confirmed "
            "ON price_channels(symbol, resolution) WHERE status='confirmed'"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS price_channels_sym_res_time "
            "ON price_channels(symbol, resolution, created_at DESC)"
        )

        # Migrations (idempotent)
        await conn.execute("ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS win_pct INTEGER")
        await conn.execute("ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS target1 DOUBLE PRECISION")
        await conn.execute("ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS target2 DOUBLE PRECISION")
        await conn.execute("ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS target3 DOUBLE PRECISION")
    log.info("PostgreSQL schema ready")


async def _insert_ai_prediction(data: dict, trigger_event: str = "manual") -> None:
    """Persist AI analysis result to ai_predictions table (fire-and-forget)."""
    if not state.pg_pool:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ai_predictions
                    (symbol, resolution, signal, conviction, trigger, analysis,
                     entry_zone, target, stop_loss, key_level, watch_buy, watch_sell,
                     analysis_bid, prediction_updated, update_reason, trade_status,
                     trade_note, trigger_event, context, win_pct,
                     target1, target2, target3)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23)
            """,
                data.get("symbol"), data.get("resolution"),
                data.get("signal"), data.get("conviction"),
                data.get("trigger"), data.get("analysis"),
                data.get("entry_zone"), data.get("target"), data.get("stop_loss"),
                data.get("key_level"), data.get("watch_buy"), data.get("watch_sell"),
                data.get("analysis_bid"),
                data.get("prediction_updated"), data.get("update_reason"),
                data.get("trade_status"), data.get("trade_note"),
                trigger_event,
                json.dumps(data.get("context")) if data.get("context") else None,
                data.get("win_pct"),
                data.get("target1"), data.get("target2"), data.get("target3"),
            )
    except Exception as e:
        log.warning(f"ai_predictions insert failed: {e}")


async def _upsert_bars_pg(symbol: str, resolution: str, bars: list[dict]) -> None:
    if not state.pg_pool or not bars:
        return
    table = config._BAR_TABLE.get(resolution)
    if not table:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            await conn.executemany(
                f"INSERT INTO {table}(symbol,time_ms,open,high,low,close,volume) "
                f"VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING",
                [(symbol, int(b['time']), float(b['open']), float(b['high']),
                  float(b['low']), float(b['close']), float(b.get('volume', 0)))
                 for b in bars]
            )
    except Exception:
        log.exception(f"PG upsert failed for {symbol}:{resolution}")


async def _query_bars_pg_before(symbol: str, resolution: str, before_ms: int, limit: int = 500) -> list[dict]:
    """Return up to `limit` most recent bars strictly before before_ms (for gap/weekend fill)."""
    if not state.pg_pool:
        return []
    table = config._BAR_TABLE.get(resolution)
    if not table:
        return []
    try:
        async with state.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT time_ms,open,high,low,close,volume FROM {table} "
                f"WHERE symbol=$1 AND time_ms < $2 ORDER BY time_ms DESC LIMIT $3",
                symbol, before_ms, limit
            )
        return [{'time': r['time_ms'], 'open': r['open'], 'high': r['high'],
                 'low': r['low'], 'close': r['close'], 'volume': r['volume']}
                for r in rows]
    except Exception:
        return []


async def _query_bars_pg(symbol: str, resolution: str, from_ms: int, to_ms: int) -> list[dict]:
    if not state.pg_pool:
        return []
    table = config._BAR_TABLE.get(resolution)
    if not table:
        return []
    try:
        async with state.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT time_ms,open,high,low,close,volume FROM {table} "
                f"WHERE symbol=$1 AND time_ms>=$2 AND time_ms<=$3 ORDER BY time_ms",
                symbol, from_ms, to_ms
            )
        return [{'time': r['time_ms'], 'open': r['open'], 'high': r['high'],
                 'low': r['low'], 'close': r['close'], 'volume': r['volume']}
                for r in rows]
    except Exception:
        log.exception(f"PG query failed for {symbol}:{resolution}")
        return []


# ── Price channel lifecycle store ───────────────────────────────────────────────

async def _get_editing_channel(symbol: str, resolution: str) -> dict | None:
    """Dòng channel đang editing cho (symbol, resolution), hoặc None."""
    if not state.pg_pool:
        return None
    try:
        async with state.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, channel FROM price_channels "
                "WHERE symbol=$1 AND resolution=$2 AND status='editing' "
                "ORDER BY created_at DESC LIMIT 1",
                symbol, resolution,
            )
        if not row:
            return None
        return {"id": row["id"], "channel": json.loads(row["channel"])}
    except Exception:
        log.exception(f"get editing channel failed {symbol}:{resolution}")
        return None


async def _insert_editing_channel(symbol: str, resolution: str, ch: dict) -> None:
    """Tạo channel editing mới. ON CONFLICT (partial unique) → đảm bảo 1 editing/symbol:res."""
    if not state.pg_pool or not ch:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO price_channels
                       (symbol, resolution, status, channel, direction,
                        width, width_pct, time_start, time_end)
                   VALUES ($1,$2,'editing',$3,$4,$5,$6,$7,$8)
                   ON CONFLICT (symbol, resolution) WHERE status='editing'
                   DO UPDATE SET channel=EXCLUDED.channel, direction=EXCLUDED.direction,
                       width=EXCLUDED.width, width_pct=EXCLUDED.width_pct,
                       time_start=EXCLUDED.time_start, time_end=EXCLUDED.time_end,
                       updated_at=NOW()""",
                symbol, resolution, json.dumps(ch), ch.get("direction"),
                ch.get("width"), ch.get("width_pct"),
                int(ch.get("time_start", 0)), int(ch.get("time_end", 0)),
            )
    except Exception:
        log.exception(f"insert editing channel failed {symbol}:{resolution}")


async def _update_editing_channel(channel_id: int, ch: dict) -> None:
    """Re-fit: cập nhật geometry của channel editing đang có (giữ nguyên id)."""
    if not state.pg_pool or not ch:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            await conn.execute(
                """UPDATE price_channels
                   SET channel=$2, direction=$3, width=$4, width_pct=$5,
                       time_start=$6, time_end=$7, updated_at=NOW()
                   WHERE id=$1 AND status='editing'""",
                channel_id, json.dumps(ch), ch.get("direction"),
                ch.get("width"), ch.get("width_pct"),
                int(ch.get("time_start", 0)), int(ch.get("time_end", 0)),
            )
    except Exception:
        log.exception(f"update editing channel failed id={channel_id}")


async def _get_confirmed_channel(symbol: str, resolution: str) -> dict | None:
    """Channel confirmed đang sống (≤1/symbol:res) — macro trend đã khoá slope."""
    if not state.pg_pool:
        return None
    try:
        async with state.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, channel FROM price_channels "
                "WHERE symbol=$1 AND resolution=$2 AND status='confirmed' "
                "ORDER BY confirmed_at DESC NULLS LAST, id DESC LIMIT 1",
                symbol, resolution,
            )
        if not row:
            return None
        return {"id": row["id"], "channel": json.loads(row["channel"])}
    except Exception:
        log.exception(f"get confirmed channel failed {symbol}:{resolution}")
        return None


async def _promote_to_confirmed(channel_id: int) -> None:
    """editing → confirmed: khoá xu hướng (đủ pivot), ghi confirmed_at."""
    if not state.pg_pool:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE price_channels SET status='confirmed', confirmed_at=NOW(), "
                "updated_at=NOW() WHERE id=$1 AND status='editing'",
                channel_id,
            )
    except Exception:
        log.exception(f"promote channel failed id={channel_id}")


async def _update_confirmed_channel(channel_id: int, ch: dict) -> None:
    """Kéo dài confirmed: cập nhật geometry (slope khoá, chỉ extend mép phải)."""
    if not state.pg_pool or not ch:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            await conn.execute(
                """UPDATE price_channels
                   SET channel=$2, direction=$3, width=$4, width_pct=$5,
                       time_start=$6, time_end=$7, updated_at=NOW()
                   WHERE id=$1 AND status='confirmed'""",
                channel_id, json.dumps(ch), ch.get("direction"),
                ch.get("width"), ch.get("width_pct"),
                int(ch.get("time_start", 0)), int(ch.get("time_end", 0)),
            )
    except Exception:
        log.exception(f"update confirmed channel failed id={channel_id}")


async def _delete_channel(channel_id: int) -> None:
    """Xoá hẳn 1 channel (editing non bị phá → xoá làm lại, không lưu lịch sử)."""
    if not state.pg_pool:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            await conn.execute("DELETE FROM price_channels WHERE id=$1", channel_id)
    except Exception:
        log.exception(f"delete channel failed id={channel_id}")


async def _commit_channel(channel_id: int, break_side: str,
                          break_price: float, break_time: int) -> None:
    """Đóng băng channel: editing → committed, ghi điểm phá biên."""
    if not state.pg_pool:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            await conn.execute(
                """UPDATE price_channels
                   SET status='committed', break_side=$2, break_price=$3,
                       break_time=$4, committed_at=NOW(), updated_at=NOW()
                   WHERE id=$1""",
                channel_id, break_side, break_price, int(break_time),
            )
    except Exception:
        log.exception(f"commit channel failed id={channel_id}")


async def _prune_committed_channels(symbol: str, resolution: str, keep: int = 1) -> int:
    """Giữ lại `keep` committed mới nhất cho (symbol, resolution), xoá phần còn lại.
    Trả về số dòng đã xoá."""
    if not state.pg_pool:
        return 0
    try:
        async with state.pg_pool.acquire() as conn:
            result = await conn.execute(
                """DELETE FROM price_channels
                   WHERE id IN (
                       SELECT id FROM price_channels
                       WHERE symbol=$1 AND resolution=$2 AND status='committed'
                       ORDER BY break_time DESC NULLS LAST, id DESC
                       OFFSET $3
                   )""",
                symbol, resolution, keep,
            )
        # result dạng "DELETE n"
        return int(result.split()[-1]) if result else 0
    except Exception:
        log.exception(f"prune committed failed {symbol}:{resolution}")
        return 0


async def _get_last_break_time(symbol: str, resolution: str) -> int | None:
    """break_time của committed channel gần nhất — mốc bắt đầu leg editing kế tiếp."""
    if not state.pg_pool:
        return None
    try:
        async with state.pg_pool.acquire() as conn:
            v = await conn.fetchval(
                "SELECT break_time FROM price_channels "
                "WHERE symbol=$1 AND resolution=$2 AND status='committed' "
                "AND break_time IS NOT NULL "
                "ORDER BY break_time DESC LIMIT 1",
                symbol, resolution,
            )
        return int(v) if v is not None else None
    except Exception:
        return None


async def _get_channels(symbol: str, resolution: str, committed_limit: int = 3) -> list[dict]:
    """Channel editing (≤1) + confirmed (≤1) + đúng `committed_limit` committed mới nhất.
    Chặn cứng ở server: dù DB còn sót committed cũ cũng không trả về quá số này."""
    if not state.pg_pool:
        return []
    try:
        async with state.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """(SELECT id, status, channel, break_side, break_price, break_time,
                           created_at, committed_at
                    FROM price_channels
                    WHERE symbol=$1 AND resolution=$2 AND status='editing'
                    ORDER BY created_at DESC LIMIT 1)
                   UNION ALL
                   (SELECT id, status, channel, break_side, break_price, break_time,
                           created_at, committed_at
                    FROM price_channels
                    WHERE symbol=$1 AND resolution=$2 AND status='confirmed'
                    ORDER BY confirmed_at DESC NULLS LAST, id DESC LIMIT 1)
                   UNION ALL
                   (SELECT id, status, channel, break_side, break_price, break_time,
                           created_at, committed_at
                    FROM price_channels
                    WHERE symbol=$1 AND resolution=$2 AND status='committed'
                    ORDER BY break_time DESC NULLS LAST, id DESC LIMIT $3)""",
                symbol, resolution, committed_limit,
            )
        return [
            {
                "id":           r["id"],
                "status":       r["status"],
                "channel":      json.loads(r["channel"]) if r["channel"] else None,
                "break_side":   r["break_side"],
                "break_price":  r["break_price"],
                "break_time":   r["break_time"],
                "created_at":   r["created_at"].isoformat() if r["created_at"] else None,
                "committed_at": r["committed_at"].isoformat() if r["committed_at"] else None,
            }
            for r in rows
        ]
    except Exception:
        log.exception(f"get channels failed {symbol}:{resolution}")
        return []


async def _pg_cleanup_loop() -> None:
    """Xóa bars cũ mỗi 24h theo retention policy."""
    while True:
        await asyncio.sleep(24 * 3600)
        if not state.pg_pool:
            continue
        try:
            async with state.pg_pool.acquire() as conn:
                for res, days in config._PG_RETENTION_DAYS.items():
                    table = config._BAR_TABLE[res]
                    cutoff = int(time.time() * 1000) - days * 86400 * 1000
                    result = await conn.execute(
                        f"DELETE FROM {table} WHERE time_ms < $1", cutoff
                    )
                    log.info(f"PG cleanup {table}: {result}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("PG cleanup failed")
