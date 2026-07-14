"""Market Structure endpoints — cached read, compute+snapshot, snapshot replay."""
import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Query

from backend.analysis.market_structure import detect as ms_detect
from backend.core import config, state
from backend.db.postgres import (
    _get_editing_channel, _insert_editing_channel,
    _update_editing_channel, _commit_channel, _get_channels,
    _prune_committed_channels,
    _get_confirmed_channel, _promote_to_confirmed,
    _update_confirmed_channel, _delete_channel,
)

router = APIRouter()
log = logging.getLogger(__name__)

_RES_MS = {'1': 60_000, '3': 180_000, '5': 300_000, '15': 900_000, '60': 3_600_000}

# Buffer khi xét phá biên: theo ATR (biến động) thay vì % width cố định (width hay bị
# 1 wick thổi phồng). Có sàn theo width khi atr=0/thiếu.
# PROVISIONAL pre-A — RE-TUNE sau khi A đổi cách tính width/biên.
_BREAK_ATR_MULT       = 0.5
_BREAK_MIN_WIDTH_FRAC = 0.05

# Cổng chất lượng: chỉ persist channel đủ tốt (chặn noise lên chart).
# PROVISIONAL pre-A — RE-TUNE sau khi A đổi metric quality.
_CH_Q_MIN     = 0.40
_CH_TOUCH_MIN = 2

# Số committed giữ lại mỗi (symbol, resolution) — phần cũ hơn bị prune sau mỗi commit,
# và cũng là số committed tối đa hiển thị trên chart.
_KEEP_COMMITTED = 3

# editing → confirmed khi có ≥ N pivot chạm rail (đủ xác nhận xu hướng).
# _NEST_SIMILAR_BARS: editing mới coi như "trùng" confirmed (cùng leg) nếu time_start lệch
# ≤ N bar và cùng direction → KHÔNG tạo editing (tránh vẽ đè macro). PROVISIONAL — re-tune sau.
_PROMOTE_TOUCH      = 4
_NEST_SIMILAR_BARS  = 4


def _channels_similar(d: dict | None, c: dict | None, resolution: str) -> bool:
    """editing mới `d` có cùng leg với confirmed `c`? (cùng direction + time_start sát nhau).
    Dùng để khỏi tạo editing đè lên confirmed (chỉ nest khi `d` là sub-leg mới hơn rõ rệt)."""
    if not d or not c:
        return False
    if d.get("direction") != c.get("direction"):
        return False
    gap_ms = _NEST_SIMILAR_BARS * _RES_MS.get(resolution, 60_000)
    return abs(int(d.get("time_start", 0)) - int(c.get("time_start", 0))) <= gap_ms


def _channel_ok(ch: dict | None) -> bool:
    """Channel đủ tốt để persist? Range chỉ xét quality; channel xét quality + số touch."""
    if not ch:
        return False
    t = ch.get("channel_type")
    if t == "range":
        return ch.get("quality", 0.0) >= _CH_Q_MIN
    if t == "channel":
        return ch.get("quality", 0.0) >= _CH_Q_MIN and ch.get("touch", 0) >= _CH_TOUCH_MIN
    return False


def _project_line(start_val: float, end_val: float,
                  t_start: int, t_end: int, t_at: int) -> float:
    """Chiếu giá trị một biên (đường thẳng) từ [t_start,t_end] tới thời điểm t_at."""
    if t_end == t_start:
        return end_val
    slope = (end_val - start_val) / (t_end - t_start)
    return end_val + slope * (t_at - t_end)


def _break_side(prev: dict, close: float, close_time: int,
                buffer: float) -> str | None:
    """Giá đóng `close` có phá rail của channel `prev` (chiếu tới close_time) + buffer?
    Trả 'upper' | 'lower' | None."""
    t_start = int(prev.get("time_start", 0))
    t_end   = int(prev.get("time_end", close_time))
    upper_at = _project_line(prev.get("upper_start", prev.get("upper", 0.0)),
                             prev.get("upper_end",   prev.get("upper", 0.0)),
                             t_start, t_end, close_time)
    lower_at = _project_line(prev.get("lower_start", prev.get("lower", 0.0)),
                             prev.get("lower_end",   prev.get("lower", 0.0)),
                             t_start, t_end, close_time)
    if close > upper_at + buffer:
        return "upper"
    if close < lower_at - buffer:
        return "lower"
    return None


def _break_buffer(ch: dict, new_atr: float) -> float:
    """Buffer phá biên theo ATR (sàn theo width) cho channel `ch`."""
    width = ch.get("width") or (ch.get("upper_end", 0.0) - ch.get("lower_end", 0.0))
    atr   = new_atr or ch.get("atr", 0.0) or 0.0
    return max(_BREAK_ATR_MULT * atr, _BREAK_MIN_WIDTH_FRAC * width) if (atr or width) else 0.0


def _project_channel_right(ch: dict, ceil_ms: int) -> dict:
    """
    Chiếu mép PHẢI của channel tới ceil_ms (= điểm phá biên) khi commit, để committed
    kết thúc đúng tại breakout, nối liền leg editing kế tiếp. Vừa CO (ceil_ms < time_end)
    vừa NỚI (ceil_ms > time_end) — breakout phát hiện bằng biên chiếu nên thường nằm
    PHẢI time_end, phải nới thì thân kênh mới chạm tới nhãn 'phá biên'. Chiếu lại
    upper/lower_end và tính lại width/mid/width_pct theo mép phải mới.
    """
    if not ch or not ceil_ms:
        return ch
    t_start = int(ch.get("time_start", 0))
    t_end   = int(ch.get("time_end", 0))
    # Chỉ bỏ qua trường hợp degenerate: breakout tại/trước điểm bắt đầu, hoặc kênh
    # không có bề rộng thời gian (tránh chia 0 ở slope). Còn lại đều chiếu được.
    if ceil_ms <= t_start or t_end <= t_start:
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
                                    close: float, close_time: int,
                                    last_pivot_time: int = 0) -> None:
    """
    Máy trạng thái 3 bậc: editing → confirmed → committed. editing & confirmed sống
    SONG SONG (nesting: trend nhỏ trong trend lớn).

    A. CONFIRMED (macro, khoá slope): phá rail → committed; không phá → kéo dài mép phải.
    B. EDITING (micro, tự do): phá rail → XOÁ (làm lại, không lưu); không phá → freeze-until-pivot.
       Tạo editing mới nếu trống & channel tốt & không trùng confirmed (tránh đè macro).
    C. PROMOTE: editing đủ pivot (touch ≥ _PROMOTE_TOUCH) & chưa có confirmed → lên confirmed.
    """
    if not state.pg_pool or not channel:
        return

    ok_new = _channel_ok(channel)
    new_atr = channel.get("atr", 0.0) or 0.0
    touch   = int(channel.get("touch", 0))

    conf = await _get_confirmed_channel(symbol, resolution)
    edit = await _get_editing_channel(symbol, resolution)

    conf_geom: dict | None = None   # geometry của confirmed còn sống cuối lượt (cho similarity)
    has_conf = False                # còn confirmed sống không (để chặn promote/tạo editing trùng)

    # ── A. CONFIRMED: commit khi phá, ngược lại kéo dài ─────────────────────────
    if conf is not None:
        cprev = conf["channel"]
        cbuf  = _break_buffer(cprev, new_atr)
        bside = _break_side(cprev, close, close_time, cbuf)
        if bside:
            await _update_confirmed_channel(conf["id"], _project_channel_right(cprev, close_time))
            await _commit_channel(conf["id"], bside, close, close_time)
            pruned = await _prune_committed_channels(symbol, resolution, _KEEP_COMMITTED)
            log.info(f"Channel COMMIT {symbol}:{resolution} id={conf['id']} confirmed "
                     f"side={bside} @ {close} (buf={cbuf:.5f}) pruned={pruned}")
        else:
            extended = _project_channel_right(cprev, close_time)
            extended["last_pivot_time"] = int(cprev.get("last_pivot_time", 0))
            await _update_confirmed_channel(conf["id"], extended)
            conf_geom, has_conf = extended, True

    # ── B. EDITING: phá → xoá; ngược lại freeze-until-pivot ─────────────────────
    if edit is not None:
        eprev = edit["channel"]
        ebuf  = _break_buffer(eprev, new_atr)
        if _break_side(eprev, close, close_time, ebuf):
            await _delete_channel(edit["id"])
            log.info(f"Channel DISCARD {symbol}:{resolution} id={edit['id']} editing (broke immature)")
            edit = None
        else:
            prev_lpt = int(eprev.get("last_pivot_time", 0))
            if ok_new and int(last_pivot_time) > prev_lpt:
                channel["last_pivot_time"] = int(last_pivot_time)
                await _update_editing_channel(edit["id"], channel)        # re-fit tự do (không clamp)
            else:
                extended = _project_channel_right(eprev, close_time)
                extended["last_pivot_time"] = prev_lpt
                await _update_editing_channel(edit["id"], extended)

    # ── C. PROMOTE editing → confirmed (đủ pivot, chưa có confirmed) ────────────
    if edit is not None and not has_conf and ok_new and touch >= _PROMOTE_TOUCH:
        await _promote_to_confirmed(edit["id"])
        log.info(f"Channel PROMOTE {symbol}:{resolution} id={edit['id']} editing→confirmed "
                 f"(touch={touch})")
        edit = None
        # confirmed vừa lên có geometry ≈ channel hiện tại → dùng để chặn tạo editing trùng.
        conf_geom, has_conf = channel, True

    # ── Tạo editing mới nếu trống & tốt & không trùng confirmed (nesting) ───────
    if edit is None and ok_new and not _channels_similar(channel, conf_geom, resolution):
        channel["last_pivot_time"] = int(last_pivot_time)
        await _insert_editing_channel(symbol, resolution, channel)        # KHÔNG clamp → cho overlap


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
                hist_result = await asyncio.to_thread(ms_detect, hist_bars, resolution)
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
    "sr_zones": {"supports": [], "resistances": []},
}

# Mỗi timeframe tự tính MS/channel độc lập (1m không còn kế thừa 3m).
# Detector đã có param riêng cho '1' (_TF_ZZ_DEPTH, horizon, lookback…).
_MS_DELEGATE: dict[str, str] = {}


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

    # Chạy trong thread pool riêng — _detect_impl là CPU-bound (zigzag/pandas),
    # nếu chạy trực tiếp trên event loop sẽ chặn webhook ghi data đến cùng lúc.
    result = await asyncio.to_thread(ms_detect, bar_list, resolution)
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
        # Thời điểm pivot xác nhận gần nhất → freeze-until-pivot (chỉ re-fit khi đổi).
        last_pivot_time = max((int(w["time"]) for w in result.get("waves", [])), default=0)
        await _update_channel_lifecycle(
            symbol, resolution, result.get("channel"),
            float(commit_bar["close"]), int(commit_bar["time"]),
            last_pivot_time,
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
    """Channel editing + tối đa _KEEP_COMMITTED committed mới nhất (mỗi TF độc lập)."""
    src = _MS_DELEGATE.get(resolution, resolution)
    return await _get_channels(symbol, src, _KEEP_COMMITTED)


@router.get("/api/ms/snapshots")
async def get_ms_snapshots(
    symbol:     str = Query(...),
    resolution: str = Query(...),
    limit:      int = Query(4, ge=1, le=20),
):
    """Return the last `limit` Market Structure snapshots from DB for chart replay."""
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
