"""Market Structure endpoints — cached read, compute+snapshot, snapshot replay."""
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Query

from backend.analysis.market_structure import detect as ms_detect
from backend.core import config, state
from backend.db.postgres import (
    _get_editing_channel, _insert_editing_channel,
    _update_editing_channel, _commit_channel, _get_channels,
    _get_last_break_time, _prune_committed_channels,
)

router = APIRouter()
log = logging.getLogger(__name__)

_RES_MS = {'1': 60_000, '3': 180_000, '5': 300_000, '15': 900_000, '60': 3_600_000}

# Buffer khi xét phá biên = phần trăm width của channel persisted.
_CHANNEL_BREAK_BUFFER = 0.10

# Số committed giữ lại mỗi (symbol, resolution) — phần cũ hơn bị prune sau mỗi commit,
# và cũng là số committed tối đa hiển thị trên chart.
_KEEP_COMMITTED = 3


def _project_line(start_val: float, end_val: float,
                  t_start: int, t_end: int, t_at: int) -> float:
    """Chiếu giá trị một biên (đường thẳng) từ [t_start,t_end] tới thời điểm t_at."""
    if t_end == t_start:
        return end_val
    slope = (end_val - start_val) / (t_end - t_start)
    return end_val + slope * (t_at - t_end)


def _clamp_channel_left(ch: dict, floor_ms: int) -> dict:
    """
    Cắt mép TRÁI của channel về floor_ms (= điểm phá biên của committed trước) để
    leg editing không chồng lên committed. Giữ nguyên slope/width (đo ở mép phải),
    chỉ dời time_start + chiếu lại upper_start/lower_start tới floor_ms.
    """
    if not ch or not floor_ms:
        return ch
    t_start = int(ch.get("time_start", 0))
    t_end   = int(ch.get("time_end", 0))
    if floor_ms <= t_start or floor_ms >= t_end:
        return ch   # không cần cắt, hoặc leg quá ngắn → để nguyên
    out = dict(ch)
    for edge in ("upper", "lower"):
        s = ch.get(f"{edge}_start")
        e = ch.get(f"{edge}_end")
        if s is None or e is None:
            continue
        slope = (e - s) / (t_end - t_start)
        out[f"{edge}_start"] = round(s + slope * (floor_ms - t_start), 5)
    out["time_start"] = int(floor_ms)
    return out


def _clamp_channel_right(ch: dict, ceil_ms: int) -> dict:
    """
    Cắt mép PHẢI của channel về ceil_ms (= điểm phá biên) khi commit, để committed
    kết thúc đúng tại breakout, nối liền leg editing kế tiếp. Chiếu lại upper/lower_end
    và tính lại width/mid/width_pct theo mép phải mới.
    """
    if not ch or not ceil_ms:
        return ch
    t_start = int(ch.get("time_start", 0))
    t_end   = int(ch.get("time_end", 0))
    if ceil_ms >= t_end or ceil_ms <= t_start:
        return ch
    out = dict(ch)
    for edge in ("upper", "lower"):
        s = ch.get(f"{edge}_start")
        e = ch.get(f"{edge}_end")
        if s is None or e is None:
            continue
        slope = (e - s) / (t_end - t_start)
        val = round(s + slope * (ceil_ms - t_start), 5)
        out[f"{edge}_end"] = val
        out[edge] = val   # 'upper'/'lower' phản chiếu mép phải
    out["time_end"] = int(ceil_ms)
    ue, le = out.get("upper_end"), out.get("lower_end")
    if ue is not None and le is not None:
        out["width"] = round(ue - le, 5)
        mid = (ue + le) / 2
        out["mid"] = round(mid, 5)
        if mid:
            out["width_pct"] = round((ue - le) / mid * 100, 4)
    return out


async def _update_channel_lifecycle(symbol: str, resolution: str,
                                    channel: dict | None,
                                    close: float, close_time: int) -> None:
    """
    Đẩy channel mới tính (`channel`) qua máy trạng thái editing/committed.

    - Chưa có editing → tạo editing mới.
    - Có editing: chiếu biên của channel persisted tới `close_time`. Nếu nến ĐÓNG
      (`close`) vượt biên trên/dưới THÊM buffer (10% width) → commit channel cũ,
      mở editing mới cho leg kế tiếp. Ngược lại → re-fit (cập nhật editing).
    """
    if not state.pg_pool or not channel:
        return
    if channel.get("channel_type") not in ("channel", "range"):
        return

    # Mốc bắt đầu leg hiện tại = điểm phá biên của committed gần nhất (nếu có).
    floor_ms = await _get_last_break_time(symbol, resolution)

    cur = await _get_editing_channel(symbol, resolution)
    if cur is None:
        await _insert_editing_channel(symbol, resolution,
                                      _clamp_channel_left(channel, floor_ms))
        return

    prev = cur["channel"]
    t_start = int(prev.get("time_start", 0))
    t_end   = int(prev.get("time_end", close_time))
    width   = prev.get("width") or (prev.get("upper_end", 0.0) - prev.get("lower_end", 0.0))
    buffer  = _CHANNEL_BREAK_BUFFER * width if width else 0.0

    upper_at = _project_line(prev.get("upper_start", prev.get("upper", 0.0)),
                             prev.get("upper_end",   prev.get("upper", 0.0)),
                             t_start, t_end, close_time)
    lower_at = _project_line(prev.get("lower_start", prev.get("lower", 0.0)),
                             prev.get("lower_end",   prev.get("lower", 0.0)),
                             t_start, t_end, close_time)

    break_side: str | None = None
    if close > upper_at + buffer:
        break_side = "upper"
    elif close < lower_at - buffer:
        break_side = "lower"

    if break_side:
        # Cắt mép phải committed về điểm phá biên (kết thúc đúng tại breakout).
        await _update_editing_channel(cur["id"], _clamp_channel_right(prev, close_time))
        await _commit_channel(cur["id"], break_side, close, close_time)
        # Leg mới bắt đầu TỪ điểm phá biên → không chồng lên committed vừa đóng.
        await _insert_editing_channel(symbol, resolution,
                                      _clamp_channel_left(channel, close_time))
        pruned = await _prune_committed_channels(symbol, resolution, _KEEP_COMMITTED)
        log.info(f"Channel COMMIT {symbol}:{resolution} id={cur['id']} "
                 f"side={break_side} @ {close} → new editing leg (pruned {pruned} old committed)")
    else:
        await _update_editing_channel(cur["id"],
                                      _clamp_channel_left(channel, floor_ms))


async def _save_ms_snapshots(symbol: str, resolution: str, result: dict,
                              all_bars: list[dict], bars: int) -> None:
    """Persist the current MS window + 3 historical non-overlapping windows to DB.
    Uses ON CONFLICT DO NOTHING so repeated calls are safe (idempotent)."""
    if not state.pg_pool:
        return
    try:
        async with state.pg_pool.acquire() as conn:
            existing_count = await conn.fetchval(
                "SELECT COUNT(*) FROM ms_snapshots WHERE symbol=$1 AND resolution=$2",
                symbol, resolution,
            )

            async def _insert_snap(res_obj: dict) -> None:
                if not res_obj.get("waves"):
                    return
                await conn.execute(
                    """
                    INSERT INTO ms_snapshots
                        (symbol, resolution, computed_at, pattern, confidence, waves, channel, structure, draw_waves)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (symbol, resolution, computed_at) DO NOTHING
                    """,
                    symbol, resolution,
                    res_obj.get("computed_at", 0),
                    res_obj["pattern"],
                    float(res_obj["confidence"]),
                    json.dumps(res_obj.get("waves", [])),
                    json.dumps(res_obj.get("channel")) if res_obj.get("channel") else None,
                    json.dumps(res_obj.get("structure")) if res_obj.get("structure") else None,
                    json.dumps(res_obj.get("draw_waves", [])),
                )

            # Remove any stale snapshot inside the current window before saving the new one
            window_ms = bars * _RES_MS.get(resolution, 60_000)
            cur_at    = result["computed_at"]
            await conn.execute(
                """DELETE FROM ms_snapshots
                   WHERE symbol=$1 AND resolution=$2
                     AND computed_at > $3 AND computed_at < $4""",
                symbol, resolution, cur_at - window_ms, cur_at,
            )
            await _insert_snap(result)

            # Backfill 3 historical non-overlapping windows
            n = len(all_bars)
            for i in range(1, 4):
                end_idx   = max(0, n - i * bars)
                start_idx = max(0, end_idx - bars)
                if end_idx <= start_idx:
                    break
                hist_bars = all_bars[start_idx:end_idx]
                if len(hist_bars) < 30:
                    break
                computed_at_hist = hist_bars[-1]["time"]
                exists = await conn.fetchval(
                    "SELECT 1 FROM ms_snapshots WHERE symbol=$1 AND resolution=$2 AND computed_at=$3",
                    symbol, resolution, computed_at_hist,
                )
                if exists:
                    continue
                hist_result = ms_detect(hist_bars, resolution)
                hist_result["symbol"]      = symbol
                hist_result["resolution"]  = resolution
                hist_result["computed_at"] = computed_at_hist
                await _insert_snap(hist_result)
    except Exception:
        log.exception("Failed to save MS snapshot")

_EMPTY_MS = {
    "pattern": "none", "confidence": 0.0, "prediction": "neutral",
    "complete": False, "direction": None, "next_target": None,
    "targets": [], "fib": None, "waves": [], "pivots_count": 0,
}

# 1m quá nhiễu → kế thừa MS từ 3m (đỉnh/đáy, BOS/CHOCH, channel)
_MS_DELEGATE = {"1": "3"}


@router.get("/api/ms")
async def get_ms(symbol: str, resolution: str):
    r = aioredis.Redis(connection_pool=state.redis_pool)
    src = _MS_DELEGATE.get(resolution, resolution)
    cached = await r.get(f"ms:{symbol}:{src}")
    if cached:
        data = json.loads(cached)
        data["resolution"] = resolution   # giữ resolution gốc để frontend biết
        return data
    return _EMPTY_MS


@router.post("/api/ms/compute")
async def compute_ms(
    symbol:     str = Query(...),
    resolution: str = Query(...),
    bars:       int = Query(300),
):
    r = aioredis.Redis(connection_pool=state.redis_pool)

    # 1m delegate: không tính MS cho 1m, trả về MS của 3m (có sẵn hoặc tính mới)
    if resolution in _MS_DELEGATE:
        src = _MS_DELEGATE[resolution]
        cached_src = await r.get(f"ms:{symbol}:{src}")
        if cached_src:
            data = json.loads(cached_src)
            data["resolution"] = resolution
            return data
        # 3m chưa có → tính 3m rồi trả về (fallback hiếm gặp)
        resolution = src

    # For backfill we need up to 4x more bars (non-overlapping windows).
    # Always fetch a large enough buffer so history can be spread across time.
    fetch_bars = max(bars * 4, 1200)

    # Fetch last `fetch_bars` closed bars from sorted set
    raw = await r.zrange(f"mt5:bars:{symbol}:{resolution}", -fetch_bars, -1)
    all_bars = [json.loads(b) for b in raw]

    # PG fallback nếu Redis miss
    if not all_bars and state.pg_pool:
        table = config._BAR_TABLE.get(resolution)
        if table:
            try:
                async with state.pg_pool.acquire() as conn:
                    rows = await conn.fetch(
                        f"SELECT time_ms,open,high,low,close,volume FROM {table} "
                        f"WHERE symbol=$1 ORDER BY time_ms DESC LIMIT $2",
                        symbol, fetch_bars
                    )
                all_bars = [{'time': r2['time_ms'], 'open': r2['open'], 'high': r2['high'],
                             'low': r2['low'], 'close': r2['close'], 'volume': r2['volume']}
                            for r2 in reversed(rows)]
            except Exception:
                log.exception(f"PG query for MS failed {symbol}:{resolution}")

    all_bars.sort(key=lambda x: x["time"])

    # Append live bar if present (only to the latest window)
    live_appended = False
    live = await r.hgetall(f"mt5:live:{symbol}:{resolution}")
    if live:
        live_bar = {
            k: int(float(v)) if k in ("volume", "time") else float(v)
            for k, v in live.items()
        }
        live_time = live_bar.get("time", 0)
        if not all_bars or all_bars[-1]["time"] != live_time:
            all_bars.append(live_bar)
            live_appended = True

    # The "current" window is the last `bars` bars
    bar_list = all_bars[-bars:] if len(all_bars) > bars else all_bars

    result = ms_detect(bar_list, resolution)
    result["symbol"]     = symbol
    result["resolution"] = resolution
    # Ensure computed_at is always the last bar's time (not 0 or missing)
    if not result.get("computed_at") and bar_list:
        result["computed_at"] = bar_list[-1]["time"]

    await r.set(f"ms:{symbol}:{resolution}", json.dumps(result), ex=config._MS_TTL)

    await _save_ms_snapshots(symbol, resolution, result, all_bars, bars)

    # Channel lifecycle — xét trên nến ĐÓNG cuối (bỏ live bar nếu vừa append)
    if bar_list:
        commit_bar = bar_list[-2] if (live_appended and len(bar_list) >= 2) else bar_list[-1]
        await _update_channel_lifecycle(
            symbol, resolution, result.get("channel"),
            float(commit_bar["close"]), int(commit_bar["time"]),
        )

    log.info(
        f"MS {symbol}:{resolution}  pattern={result['pattern']}"
        f"  confidence={result['confidence']:.2f}  bars={len(bar_list)}"
        f"  pivots={result['pivots_count']}"
    )
    return result


@router.get("/api/channels")
async def get_channels(
    symbol:     str = Query(...),
    resolution: str = Query(...),
):
    """Channel editing + tối đa _KEEP_COMMITTED committed mới nhất. 1m kế thừa 3m như MS."""
    src = _MS_DELEGATE.get(resolution, resolution)
    return await _get_channels(symbol, src, _KEEP_COMMITTED)


@router.get("/api/ms/snapshots")
async def get_ms_snapshots(
    symbol:     str = Query(...),
    resolution: str = Query(...),
    limit:      int = Query(4, ge=1, le=20),
):
    """Return the last `limit` Market Structure snapshots from DB for chart replay.
    1m kế thừa 3m (như /api/ms, /api/channels) — tránh lấy snapshot '1' cũ/lạc."""
    resolution = _MS_DELEGATE.get(resolution, resolution)
    if not state.pg_pool:
        return []
    try:
        async with state.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT computed_at, pattern, confidence, waves, channel, structure, draw_waves
                FROM ms_snapshots
                WHERE symbol=$1 AND resolution=$2
                ORDER BY computed_at DESC
                LIMIT $3
                """,
                symbol, resolution, limit,
            )
        return [
            {
                "computed_at": r["computed_at"],
                "pattern":     r["pattern"],
                "confidence":  r["confidence"],
                "waves":       json.loads(r["waves"]) if r["waves"] else [],
                "channel":     json.loads(r["channel"]) if r["channel"] else None,
                "structure":   json.loads(r["structure"]) if r["structure"] else None,
                "draw_waves":  json.loads(r["draw_waves"]) if r["draw_waves"] else [],
            }
            for r in rows
        ]
    except Exception:
        log.exception("Failed to fetch MS snapshots")
        return []
