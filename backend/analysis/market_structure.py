"""
Market Structure + Price Channel detector — optimised for scalping (1m–15m).

Swing H/L:  MT5 ZigZag algorithm (port of ZigZagColor.mq5, MetaQuotes).
BOS/CHOCH:  custom real-time detection (compares cur_close to last swing levels).
Channel:    linear-regression parallel channel.
Wedge:      convergence detector.
"""
from __future__ import annotations
import logging
import time as _time

import pandas as _pd
import pandas_ta as _ta

log = logging.getLogger(__name__)


# ── Per-timeframe ZigZag parameters ───────────────────────────────────────────

# depth: window size to find min/max extremes
_TF_ZZ_DEPTH: dict[str, int] = {
    '1':  8,
    '3':  10,
    '5':  12,
    '15': 12,
    '60': 15,
}
_ZZ_DEPTH_DEFAULT = 12

# backstep: min bars between two same-type candidates in map buffer
_TF_ZZ_BACKSTEP: dict[str, int] = {
    '1':  2,
    '3':  2,
    '5':  3,
    '15': 3,
    '60': 3,
}
_ZZ_BACKSTEP_DEFAULT = 3

# Minimum swing size = N × ATR để lọc pivot nhiễu
_TF_MIN_ATR_MULT: dict[str, float] = {
    '1':  1.0,
    '3':  1.2,
    '5':  1.5,
    '15': 2.0,
    '60': 2.5,
}
_ATR_MULT_DEFAULT = 1.5

# Khoảng cách bar tối thiểu giữa 2 pivot liên tiếp
_TF_MIN_BAR_GAP: dict[str, int] = {
    '1':  3,
    '3':  4,
    '5':  5,
    '15': 5,
    '60': 4,
}
_BAR_GAP_DEFAULT = 5

_TF_SCALP_HORIZON: dict[str, int] = {
    '1': 15, '3': 14, '5': 12, '15': 10, '60': 6,
}
_HORIZON_DEFAULT = 10

# Window hồi quy kênh giá (bar) = (min, max); giá trị thực tế co giãn theo
# volatility ratio (xem _volatility_ratio) — midpoint khớp giá trị cố định cũ.
_TF_BAR_LOOKBACK: dict[str, tuple[int, int]] = {
    '1':  (40, 80),
    '3':  (40, 80),
    '5':  (55, 105),
    '15': (55, 105),
    '60': (70, 130),
}
_BAR_LOOKBACK_DEFAULT = (55, 105)


# ── DataFrame conversion ───────────────────────────────────────────────────────

def _bars_to_df(bars: list[dict]) -> _pd.DataFrame:
    return _pd.DataFrame([{
        'open':   b['open'],
        'high':   b['high'],
        'low':    b['low'],
        'close':  b['close'],
        'volume': b.get('volume', 0),
    } for b in bars]).astype(float)


# ── MT5 ZigZag pivot detection (port of ZigZagColor.mq5) ──────────────────────

def _zigzag_mt5(bars: list[dict], depth: int = 12, backstep: int = 3) -> list[dict]:
    """
    Port of MT5 ZigZagColor.mq5 (MetaQuotes).

    Phase 1: build HighMapBuffer / LowMapBuffer — candidate pivots.
      A bar[i] is a LOW candidate if low[i] == min(low[i-depth+1..i]) and
      it is a NEW minimum (differs from the last seen minimum), with backstep
      cleanup (remove prior candidates within `backstep` bars if less extreme).
      HIGH candidates are symmetric.

    Phase 2: state machine (Extremum → Peak → Bottom → Peak …) selects the
      alternating ZigZag peaks and bottoms, updating when a more extreme point
      is found before the direction reverses.

    bars: list of OHLCV dicts, chronological (index 0 = oldest).
    Returns list of {'time', 'price', 'type': 'high'|'low', 'idx'}.
    """
    n = len(bars)
    if n < depth:
        return []

    high = [b['high'] for b in bars]
    low  = [b['low']  for b in bars]

    high_map = [0.0] * n
    low_map  = [0.0] * n
    zz_peak   = [0.0] * n
    zz_bottom = [0.0] * n

    # ── Phase 1: candidate extremes ───────────────────────────────────────────
    last_high = 0.0
    last_low  = 0.0

    for shift in range(depth - 1, n):
        win_start = max(0, shift - depth + 1)

        # LOW candidate
        val = min(low[win_start:shift + 1])
        if val == last_low:
            val = 0.0
        else:
            last_low = val
            if low[shift] != val:   # current bar not at the window minimum
                val = 0.0
            else:
                for back in range(1, backstep + 1):
                    idx = shift - back
                    if idx >= 0 and low_map[idx] != 0.0 and low_map[idx] > val:
                        low_map[idx] = 0.0
        low_map[shift] = val if low[shift] == val and val != 0.0 else 0.0

        # HIGH candidate
        val = max(high[win_start:shift + 1])
        if val == last_high:
            val = 0.0
        else:
            last_high = val
            if high[shift] != val:  # current bar not at the window maximum
                val = 0.0
            else:
                for back in range(1, backstep + 1):
                    idx = shift - back
                    if idx >= 0 and high_map[idx] != 0.0 and high_map[idx] < val:
                        high_map[idx] = 0.0
        high_map[shift] = val if high[shift] == val and val != 0.0 else 0.0

    # ── Phase 2: alternating ZigZag selection ─────────────────────────────────
    EXTREMUM = 0
    PEAK     = 1
    BOTTOM   = -1

    extreme_search = EXTREMUM
    last_high_val = 0.0
    last_low_val  = 0.0
    last_high_pos = 0
    last_low_pos  = 0

    for shift in range(depth - 1, n):
        if extreme_search == EXTREMUM:
            if last_low_val == 0.0 and last_high_val == 0.0:
                if high_map[shift] != 0.0:
                    last_high_val = high[shift]
                    last_high_pos = shift
                    extreme_search = BOTTOM
                    zz_peak[shift] = last_high_val
                if low_map[shift] != 0.0:
                    last_low_val = low[shift]
                    last_low_pos = shift
                    extreme_search = PEAK
                    zz_bottom[shift] = last_low_val

        elif extreme_search == PEAK:
            # After a bottom — looking for a lower low (update) OR a high (switch direction)
            if (low_map[shift] != 0.0 and low_map[shift] < last_low_val
                    and high_map[shift] == 0.0):
                zz_bottom[last_low_pos] = 0.0
                last_low_pos  = shift
                last_low_val  = low_map[shift]
                zz_bottom[shift] = last_low_val
            if high_map[shift] != 0.0 and low_map[shift] == 0.0:
                last_high_val = high_map[shift]
                last_high_pos = shift
                zz_peak[shift] = last_high_val
                extreme_search = BOTTOM

        elif extreme_search == BOTTOM:
            # After a peak — looking for a higher high (update) OR a low (switch direction)
            if (high_map[shift] != 0.0 and high_map[shift] > last_high_val
                    and low_map[shift] == 0.0):
                zz_peak[last_high_pos] = 0.0
                last_high_pos = shift
                last_high_val = high_map[shift]
                zz_peak[shift] = last_high_val
            if low_map[shift] != 0.0 and high_map[shift] == 0.0:
                last_low_val  = low_map[shift]
                last_low_pos  = shift
                zz_bottom[shift] = last_low_val
                extreme_search = PEAK

    # ── Collect pivots ─────────────────────────────────────────────────────────
    result: list[dict] = []
    for i in range(n):
        if zz_peak[i] != 0.0:
            result.append({'time': bars[i]['time'], 'price': zz_peak[i],
                           'type': 'high', 'idx': i})
        elif zz_bottom[i] != 0.0:
            result.append({'time': bars[i]['time'], 'price': zz_bottom[i],
                           'type': 'low', 'idx': i})

    return result


def _clean_pivots(pivots: list[dict]) -> list[dict]:
    """
    Đảm bảo high/low xen kẽ nhau.
    Nếu 2 high liên tiếp → giữ high cao hơn.
    Nếu 2 low liên tiếp  → giữ low thấp hơn.
    """
    if not pivots:
        return []
    result = [pivots[0]]
    for p in pivots[1:]:
        last = result[-1]
        if p['type'] == last['type']:
            if p['type'] == 'high' and p['price'] > last['price']:
                result[-1] = p
            elif p['type'] == 'low' and p['price'] < last['price']:
                result[-1] = p
        else:
            result.append(p)
    return result


def _calc_atr(bars: list[dict], period: int = 14) -> float:
    """ATR (Wilder smoothing) của `period` bar gần nhất, qua pandas-ta."""
    if len(bars) < 2:
        return 0.0
    df  = _bars_to_df(bars)
    atr = _ta.atr(df['high'], df['low'], df['close'], length=period)
    val = atr.iloc[-1] if atr is not None else None
    return float(val) if val is not None and not _pd.isna(val) else 0.0


def _volatility_ratio(bars: list[dict]) -> float:
    """
    Biến động ngắn hạn / dài hạn (ATR14 gần nhất so với ATR40 nền). >1: vol đang
    giãn (breakout/tăng tốc) → nên co window hồi quy kênh lại để bám kịp regime
    mới; <1: đang co (sideway) → giãn window để có đủ dữ liệu tìm slope thật giữa
    nhiễu. Thiếu dữ liệu lịch sử → 1.0 (trung tính, dùng midpoint mặc định).
    """
    if len(bars) < 60:
        return 1.0
    atr_short = _calc_atr(bars[-30:], period=14)
    atr_long  = _calc_atr(bars[-90:] if len(bars) >= 90 else bars, period=40)
    if atr_long <= 1e-9:
        return 1.0
    return atr_short / atr_long


def _filter_noise(pivots: list[dict], min_move: float, min_bars: int = 3) -> list[dict]:
    """
    Loại bỏ pivot nhiễu: swing giữa 2 pivot liên tiếp phải >= min_move
    VÀ khoảng cách bar >= min_bars.
    Nếu không đủ → bỏ qua, hoặc giữ cái cực trị hơn nếu cùng type.
    """
    if len(pivots) < 2:
        return pivots
    result = [pivots[0]]
    for p in pivots[1:]:
        swing    = abs(p['price'] - result[-1]['price'])
        bar_gap  = p['idx'] - result[-1]['idx']
        if swing >= min_move and bar_gap >= min_bars:
            result.append(p)
        elif p['type'] == result[-1]['type']:
            if p['type'] == 'high' and p['price'] > result[-1]['price']:
                result[-1] = p
            elif p['type'] == 'low' and p['price'] < result[-1]['price']:
                result[-1] = p
    return _clean_pivots(result)




# ── Linear regression (pure Python, no numpy) ──────────────────────────────────

def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Returns (slope, intercept, r_squared)."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0, 0.0
    sx  = sum(xs);          sy  = sum(ys)
    sxy = sum(x*y for x, y in zip(xs, ys))
    sx2 = sum(x*x for x in xs)
    denom = n * sx2 - sx * sx
    if abs(denom) < 1e-12:
        return 0.0, sy / n, 0.0
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n

    mean_y  = sy / n
    ss_tot  = sum((y - mean_y) ** 2 for y in ys)
    ss_res  = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2      = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return slope, intercept, max(0.0, min(1.0, r2))


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _theil_sen_slope(xs: list[float], ys: list[float]) -> float:
    """
    Slope robust = trung vị của TẤT CẢ slope từng cặp điểm (Theil–Sen). Khác OLS
    (bình phương residual → 1 điểm outlier bị khuếch đại bậc 2), ở đây outlier chỉ
    đóng góp như bất kỳ cặp nào khác rồi bị trung vị hoá — 1 wick bất thường
    không còn kéo lệch cả slope của rail.
    """
    n = len(xs)
    if n < 2:
        return 0.0
    pair_slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(n) for j in range(i + 1, n)
        if abs(xs[j] - xs[i]) > 1e-9
    ]
    return _median(pair_slopes) if pair_slopes else 0.0


def _fit_r2(xs: list[float], ys: list[float], slope: float) -> float:
    """
    R² của đường có slope cho trước (intercept = trung vị residual, kiểu
    Theil–Sen) so với baseline trung bình — dùng để GATE: slope không có ý
    nghĩa thống kê (r² thấp, giá thực chất là nhiễu ngang) thì không nên vẽ
    kênh nghiêng lên nó.
    """
    n = len(xs)
    if n < 2:
        return 0.0
    intercept = _median([y - slope * x for x, y in zip(xs, ys)])
    mean_y  = sum(ys) / n
    ss_tot  = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot < 1e-12:
        return 0.0
    ss_res  = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return max(0.0, min(1.0, 1.0 - ss_res / ss_tot))


# ── Swing classification ────────────────────────────────────────────────────────

def _classify_swings(pivots: list[dict]) -> list[dict]:
    result: list[dict] = []
    last_high: float | None = None
    last_low:  float | None = None

    for p in pivots:
        p2 = dict(p)
        if p['type'] == 'high':
            if last_high is None:
                p2['label'] = 'H'
            elif p['price'] >= last_high:
                p2['label'] = 'HH'
            else:
                p2['label'] = 'LH'
            last_high = p['price']
        else:
            if last_low is None:
                p2['label'] = 'L'
            elif p['price'] <= last_low:
                p2['label'] = 'LL'
            else:
                p2['label'] = 'HL'
            last_low = p['price']
        result.append(p2)
    return result


def _detect_trend(classified: list[dict], lookback: int = 6) -> str:
    recent = classified[-lookback:]
    labels = [p['label'] for p in recent]
    bull = labels.count('HH') + labels.count('HL')
    bear = labels.count('LH') + labels.count('LL')
    if bull > bear and bull >= 2:   return 'bullish'
    if bear > bull and bear >= 2:   return 'bearish'
    return 'ranging'


# ── BOS / CHOCH detection ──────────────────────────────────────────────────────

def _find_bos_choch(classified: list[dict], cur_close: float,
                    trend: str) -> tuple[str, dict | None, float]:
    """
    Real-time: compares cur_close to the last confirmed swing levels.
    Returns (event_type, bos_pivot, break_strength).
    """
    if len(classified) < 4:
        return 'none', None, 0.0

    highs = [p for p in classified if p['type'] == 'high']
    lows  = [p for p in classified if p['type'] == 'low']
    if not highs or not lows:
        return 'none', None, 0.0

    last_sh = highs[-1]
    last_sl = lows[-1]

    all_prices = [p['price'] for p in classified[-10:]]
    price_range = max(all_prices) - min(all_prices) if len(all_prices) > 1 else 1.0

    event = 'none'
    pivot = None
    strength = 0.0

    if cur_close > last_sh['price']:
        raw_strength = (cur_close - last_sh['price']) / price_range if price_range > 0 else 0
        strength = min(raw_strength * 3, 1.0)
        # ranging → CHOCH (đổi tính cách từ sideway sang bullish), không phải BOS
        event = 'bos_bullish' if trend == 'bullish' else 'choch_bullish'
        pivot = last_sh

    elif cur_close < last_sl['price']:
        raw_strength = (last_sl['price'] - cur_close) / price_range if price_range > 0 else 0
        strength = min(raw_strength * 3, 1.0)
        # ranging → CHOCH (đổi tính cách từ sideway sang bearish), không phải BOS
        event = 'bos_bearish' if trend == 'bearish' else 'choch_bearish'
        pivot = last_sl

    return event, pivot, strength


# ── Price channel ──────────────────────────────────────────────────────────────

def _range_box(all_h: list[dict], all_l: list[dict],
               bars: list[dict], cur_close: float, last_idx: int) -> dict:
    """Fallback khi không vẽ được channel: range box từ highest_high → lowest_low."""
    upper = max(p['price'] for p in all_h)
    lower = min(p['price'] for p in all_l)
    width = upper - lower
    if width < 1e-9:
        return {}
    mid   = (upper + lower) / 2
    pos   = max(0.0, min(1.0, (cur_close - lower) / width))
    xi_start = float(min(p['idx'] for p in all_h + all_l))
    start_bar = max(0, min(int(xi_start), len(bars) - 1))
    return {
        'upper': round(upper, 5), 'lower': round(lower, 5), 'mid': round(mid, 5),
        'slope_pct': 0.0, 'quality': 0.4, 'touch': 0, 'pos': round(pos, 4), 'direction': 'flat',
        'upper_start': round(upper, 5), 'lower_start': round(lower, 5),
        'upper_end':   round(upper, 5), 'lower_end':   round(lower, 5),
        'time_start': int(bars[start_bar]['time']),
        'time_end':   int(bars[last_idx]['time']),
        'channel_type': 'range',
        'width':      round(width, 5),
        'width_pct':  round(width / mid * 100 if mid > 0 else 0.0, 4),
        'r2':         0.0,
    }


def _channel_anchor(recent: list[dict], all_h: list[dict], all_l: list[dict],
                    trend_local: str, last_idx: int) -> int:
    """
    Điểm gốc (anchor) của leg hiện tại — bullish → đáy thấp nhất trong window;
    bearish → đỉnh cao nhất; ranging → pivot sớm nhất. Đây là gốc kênh, KHÔNG
    phải 2 pivot cuối. Đảm bảo tối thiểu 5 bar để hồi quy có ý nghĩa; nếu cực trị
    quá sát hiện tại thì lùi về pivot sớm nhất trong window.
    """
    if trend_local == 'bearish' and all_h:
        anchor_idx = max(all_h, key=lambda p: p['price'])['idx']
    elif trend_local == 'bullish' and all_l:
        anchor_idx = min(all_l, key=lambda p: p['price'])['idx']
    else:
        anchor_idx = min(p['idx'] for p in recent)

    anchor_idx = max(0, min(int(anchor_idx), last_idx))
    if last_idx - anchor_idx < 5:
        anchor_idx = max(0, min(int(min(p['idx'] for p in recent)), last_idx - 5))
    return anchor_idx


def _channel_slope(bars: list[dict], leg_h: list[dict], leg_l: list[dict],
                   anchor_idx: int, last_idx: int) -> tuple[float, float]:
    """
    Slope song song = trung bình CÓ TRỌNG SỐ (theo số pivot) của hồi quy
    Theil–Sen riêng dải ĐỈNH & dải ĐÁY → robust hơn OLS (không bị 1 wick bất
    thường kéo lệch), rail nào nhiều pivot hơn được tin nhiều hơn thay vì
    50/50 cứng. Thiếu pivot 1 phía → dùng phía kia; thiếu cả hai → fallback
    hồi quy trên close. Trả thêm r² (đo trên slope đã chọn) để caller gate
    kênh không có ý nghĩa thống kê.
    """
    parts: list[tuple[float, int, list[float], list[float]]] = []
    if len(leg_h) >= 2:
        hx = [float(p['idx']) for p in leg_h]
        hy = [p['price'] for p in leg_h]
        parts.append((_theil_sen_slope(hx, hy), len(leg_h), hx, hy))
    if len(leg_l) >= 2:
        lx = [float(p['idx']) for p in leg_l]
        ly = [p['price'] for p in leg_l]
        parts.append((_theil_sen_slope(lx, ly), len(leg_l), lx, ly))

    if parts:
        total_w = sum(w for _, w, _, _ in parts)
        slope   = sum(s * w for s, w, _, _ in parts) / total_w
        r2      = min(_fit_r2(xs, ys, slope) for _, _, xs, ys in parts)
        return slope, r2

    window = bars[anchor_idx:last_idx + 1]
    wx = [float(anchor_idx + i) for i in range(len(window))]
    wy = [float(b['close']) for b in window]
    slope = _theil_sen_slope(wx, wy)
    return slope, _fit_r2(wx, wy, slope)


def _robust_edge(pivots: list[dict], slope: float, take_max: bool) -> float:
    """
    Offset của 1 rail = cực trị residual (giá - slope×idx) của các pivot phía đó
    (đỉnh đẩy rail trên, đáy đẩy rail dưới). Bỏ 1 điểm cực đoan nhất nếu đủ pivot
    (≥4) để một swing bất thường không một mình kéo méo cả rail; dưới 4 điểm thì
    giữ đúng cực trị (không đủ dư để trim).
    """
    residuals = [p['price'] - slope * float(p['idx']) for p in pivots]
    if len(residuals) >= 4:
        residuals = sorted(residuals, reverse=take_max)[1:]
    return max(residuals) if take_max else min(residuals)


# slope/bar tính theo bội số ATR — resolution-invariant, thay % giá cố định cũ
# (0.005% như nhau cho M1 lẫn H1 là sai vì biến động/bar mỗi khung thời gian khác nhau).
_SLOPE_ATR_MULT = 0.15

# r² tối thiểu để coi slope là có ý nghĩa thống kê. Dưới ngưỡng này giá thực
# chất là nhiễu ngang → trả range box thay vì vẽ 1 đường nghiêng giả (fakeout
# risk cao với scalping vì vào lệnh theo 1 "xu hướng" không tồn tại).
_MIN_FIT_R2 = 0.15


def _channel_direction(slope: float, atr_val: float) -> str:
    if atr_val <= 0:
        return 'flat'
    ratio = slope / atr_val
    if ratio > _SLOPE_ATR_MULT:  return 'up'
    if ratio < -_SLOPE_ATR_MULT: return 'down'
    return 'flat'


def _channel_quality(leg_h: list[dict], leg_l: list[dict], slope: float,
                     upper_off: float, lower_off: float, width: float) -> tuple[float, int]:
    """
    Quality (rail-respect, KHÔNG dùng r²(close)):
      tightness — pivot bám sát rail của nó tới đâu, chuẩn hoá theo nửa width →
                  CHẠY cả khi đi ngang (r² close ≈ 0 vẫn cho điểm cao nếu range gọn).
      touch     — số pivot nằm trong tol của rail.
      confirm   — kênh ÍT pivot (vd 3) thì rail trùng đúng cực trị → tightness/touch
                  ẢO cao (mọi pivot nằm trên rail theo construction). Hạ điểm theo số
                  pivot để 'minimal channel' không bị chấm gần hoàn hảo; cần ~6 pivot
                  mới đạt hệ số tối đa.
    """
    half   = width / 2 if width else 1.0
    up_res = [abs((p['price'] - slope * float(p['idx'])) - upper_off) for p in leg_h]
    lo_res = [abs((p['price'] - slope * float(p['idx'])) - lower_off) for p in leg_l]
    all_res   = up_res + lo_res
    mean_res  = sum(all_res) / len(all_res) if all_res else half
    tightness = max(0.0, 1.0 - mean_res / half)

    tol = width * 0.12
    edge_touch  = sum(1 for r in up_res if r < tol) + sum(1 for r in lo_res if r < tol)
    touch_score = min(1.0, edge_touch / 4)

    n_piv   = len(leg_h) + len(leg_l)
    confirm = min(1.0, max(0.0, (n_piv - 2) / 4.0))
    quality = round((0.5 * tightness + 0.5 * touch_score) * (0.5 + 0.5 * confirm), 4)
    return quality, edge_touch


def _build_channel(classified: list[dict], bars: list[dict], cur_close: float,
                   last_idx: int, resolution: str, atr_val: float) -> dict:
    t_end = bars[last_idx]['time'] if bars else 0
    _empty = {'upper': cur_close, 'lower': cur_close, 'mid': cur_close,
              'slope_pct': 0.0, 'quality': 0.0, 'touch': 0, 'pos': 0.5, 'direction': 'flat',
              'upper_start': cur_close, 'lower_start': cur_close,
              'upper_end': cur_close, 'lower_end': cur_close,
              'time_start': t_end, 'time_end': t_end, 'channel_type': 'none',
              'width': 0.0, 'width_pct': 0.0, 'r2': 0.0}

    lo, hi = _TF_BAR_LOOKBACK.get(resolution, _BAR_LOOKBACK_DEFAULT)
    vol_ratio = _volatility_ratio(bars[:last_idx + 1])
    # vol_ratio trong [0.7, 1.7] map tuyến tính sang lookback [hi, lo] — giãn
    # vol → window ngắn lại (bám regime mới nhanh hơn), co vol → window dài ra.
    t = min(1.0, max(0.0, vol_ratio - 0.7))
    bar_lookback = int(round(hi - (hi - lo) * t))
    recent = [p for p in classified[-16:] if last_idx - p['idx'] <= bar_lookback]
    if len(recent) < 3:
        recent = classified[-8:]
    if len(recent) < 3:
        return _empty

    all_h = [p for p in recent if p['type'] == 'high']
    all_l = [p for p in recent if p['type'] == 'low']
    if not all_h or not all_l:
        return _empty

    # Trend cục bộ TRONG window đang xét (không phải toàn bộ lịch sử classified) —
    # anchor phải khớp phạm vi với rail đang fit, tránh lệch giữa 2 khái niệm trend.
    trend_local = _detect_trend(recent)
    anchor_idx  = _channel_anchor(recent, all_h, all_l, trend_local, last_idx)
    if last_idx - anchor_idx < 3:
        return _range_box(all_h, all_l, bars, cur_close, last_idx) or _empty

    # Kênh song song fit TỪ PIVOT trong leg [anchor_idx, last_idx] (không phải
    # close/wick) — rail mô tả swing thật, đã lọc noise.
    leg_h = [p for p in all_h if p['idx'] >= anchor_idx] or all_h
    leg_l = [p for p in all_l if p['idx'] >= anchor_idx] or all_l
    slope, fit_r2 = _channel_slope(bars, leg_h, leg_l, anchor_idx, last_idx)
    if fit_r2 < _MIN_FIT_R2:
        # Slope không có ý nghĩa thống kê (giá thực chất nhiễu ngang) → trả
        # range box thay vì vẽ 1 kênh nghiêng giả, tránh fakeout khi scalping.
        return _range_box(all_h, all_l, bars, cur_close, last_idx) or _empty

    upper_off = _robust_edge(leg_h, slope, take_max=True)
    lower_off = _robust_edge(leg_l, slope, take_max=False)

    xi_start = float(anchor_idx)
    xi_end   = float(last_idx)
    upper_start = slope * xi_start + upper_off
    upper_end   = slope * xi_end   + upper_off
    lower_start = slope * xi_start + lower_off
    lower_end   = slope * xi_end   + lower_off

    width = upper_end - lower_end
    if width < 1e-9:
        return _range_box(all_h, all_l, bars, cur_close, last_idx) or _empty

    mid       = (upper_end + lower_end) / 2
    pos       = max(0.0, min(1.0, (cur_close - lower_end) / width))
    slope_pct = slope / mid * 100 if mid > 0 else 0.0
    width_pct = width / mid * 100 if mid > 0 else 0.0
    direction = _channel_direction(slope, atr_val)
    quality, edge_touch = _channel_quality(leg_h, leg_l, slope, upper_off, lower_off, width)

    start_bar_idx = max(0, min(int(xi_start), len(bars) - 1))
    time_start = bars[start_bar_idx]['time']
    time_end   = bars[last_idx]['time']

    return {
        'upper':        round(upper_end, 5),
        'lower':        round(lower_end, 5),
        'mid':          round(mid, 5),
        'slope_pct':    round(slope_pct, 5),
        'quality':      quality,
        'touch':        edge_touch,
        'pos':          round(pos, 4),
        'direction':    direction,
        'upper_start':  round(upper_start, 5),
        'lower_start':  round(lower_start, 5),
        'upper_end':    round(upper_end, 5),
        'lower_end':    round(lower_end, 5),
        'time_start':   int(time_start),
        'time_end':     int(time_end),
        'channel_type': 'channel',
        'width':        round(width, 5),
        'width_pct':    round(width_pct, 4),
        'r2':           round(fit_r2, 4),
    }


# ── Wedge (nêm giá) detector ──────────────────────────────────────────────────

def _detect_wedge(classified: list[dict], bars: list[dict]) -> dict | None:
    highs = [p for p in classified if p['type'] == 'high'][-3:]
    lows  = [p for p in classified if p['type'] == 'low'][-3:]

    if len(highs) < 3 or len(lows) < 3:
        return None

    last_idx = len(bars) - 1

    h_xs = [float(p['idx']) for p in highs]
    h_ys = [p['price']      for p in highs]
    l_xs = [float(p['idx']) for p in lows]
    l_ys = [p['price']      for p in lows]

    h_slope, h_inter, h_r2 = _linreg(h_xs, h_ys)
    l_slope, l_inter, l_r2 = _linreg(l_xs, l_ys)

    quality = (h_r2 + l_r2) / 2
    if quality < 0.50:
        return None

    x_start = float(max(h_xs[0], l_xs[0]))
    upper_start = h_slope * x_start + h_inter
    lower_start = l_slope * x_start + l_inter

    upper_end = h_slope * last_idx + h_inter
    lower_end = l_slope * last_idx + l_inter

    width_start = upper_start - lower_start
    width_end   = upper_end   - lower_end

    if width_start <= 0 or width_end <= 0:
        return None
    if width_end >= width_start * 0.85:
        return None

    if h_slope > 0 and l_slope > 0 and h_slope < l_slope:
        wedge_type = 'rising'
    elif h_slope < 0 and l_slope < 0 and h_slope < l_slope:
        wedge_type = 'falling'
    elif h_slope <= 0 and l_slope >= 0:
        wedge_type = 'symmetric'
    else:
        return None

    apex_bars: int | None = None
    slope_diff = h_slope - l_slope
    if abs(slope_diff) > 1e-9:
        apex_x   = (l_inter - h_inter) / slope_diff
        apex_bars = int(round(apex_x - last_idx))

    x_start_i = max(0, min(int(x_start), last_idx))
    time_start = bars[x_start_i]['time']
    time_end   = bars[last_idx]['time']

    return {
        'type':      wedge_type,
        'quality':   round(quality, 3),
        'apex_bars': apex_bars,
        'upper': {
            'time_start':  int(time_start),
            'price_start': round(upper_start, 5),
            'time_end':    int(time_end),
            'price_end':   round(upper_end, 5),
        },
        'lower': {
            'time_start':  int(time_start),
            'price_start': round(lower_start, 5),
            'time_end':    int(time_end),
            'price_end':   round(lower_end, 5),
        },
    }


# ── Rule-based signal ─────────────────────────────────────────────────────────

def _rule_signal(classified: list[dict], channel: dict) -> dict:
    """
    BUY:  channel.pos < 0.30  AND ≥2 trong 5 label gần nhất là HH/HL
    SELL: channel.pos > 0.70  AND ≥2 trong 5 label gần nhất là LH/LL
    WAIT: else
    """
    pos    = channel.get('pos', 0.5)
    labels = [p['label'] for p in classified[-5:]]
    bull   = labels.count('HH') + labels.count('HL')
    bear   = labels.count('LH') + labels.count('LL')

    if pos < 0.30 and bull >= 2:
        sig = 'BUY'
    elif pos > 0.70 and bear >= 2:
        sig = 'SELL'
    else:
        sig = 'WAIT'

    return {'signal': sig, 'pos': round(pos, 3), 'labels': labels[-3:]}


# ── No-pattern sentinel ────────────────────────────────────────────────────────

_NO_PATTERN: dict = {
    'pattern':      'none',
    'confidence':   0.0,
    'shape_score':  0.0,
    'significance': 0.0,
    'prediction':   'neutral',
    'complete':     False,
    'direction':    None,
    'next_target':  None,
    'targets':      [],
    'fib':          None,
    'waves':        [],
    'channel':      None,
    'structure':    None,
    'pivots_count': 0,
    'recency':      None,
    'scalp':        False,
    'rule_signal':  {'signal': 'WAIT', 'pos': 0.5, 'labels': []},
}


# ── Main entry point ───────────────────────────────────────────────────────────

# Draw-only zigzag — nhạy hơn pivot cấu trúc để chart không "đứt" ở vùng ranging.
# KHÔNG dùng cho pattern/AI (giữ ổn định tín hiệu) — chỉ để vẽ line.
# depth NHỎ hơn structural (12) để bắt nhiều swing như ZigZag MT5 (mịn cho mắt);
# structural giữ depth lớn cho channel/BOS sạch. Tách bạch để không ảnh hưởng tín hiệu.
_TF_DRAW_DEPTH: dict[str, int] = {
    '1':  4,
    '3':  4,
    '5':  5,
    '15': 5,
    '60': 6,
}
_DRAW_DEPTH_DEFAULT = 5
_DRAW_ATR_MULT = 0.3   # (cũ 0.5) lọc nhẹ hơn → giữ swing nhỏ, zigzag dày như MT5
_DRAW_MIN_BARS = 2


def _compute_draw_waves(bars: list[dict], resolution: str) -> list[dict]:
    """ZigZag mịn cho chart: depth NHỎ + lọc nhiễu nhẹ → dày swing như ZigZag MT5."""
    if not bars or len(bars) < 5:
        return []
    depth    = _TF_DRAW_DEPTH.get(resolution, _DRAW_DEPTH_DEFAULT)
    backstep = _TF_ZZ_BACKSTEP.get(resolution, _ZZ_BACKSTEP_DEFAULT)
    pivots   = _clean_pivots(_zigzag_mt5(bars, depth, backstep))
    if not pivots:
        return []
    atr  = _calc_atr(bars[-50:])
    fine = _filter_noise(pivots, atr * _DRAW_ATR_MULT, _DRAW_MIN_BARS)
    return [{'time': p['time'], 'price': p['price'], 'type': p['type']} for p in fine[-80:]]


def detect(bars: list[dict], resolution: str = '60') -> dict:
    """MS detect + draw-only zigzag (`draw_waves`) cho chart. Wrapper quanh _detect_impl."""
    result = _detect_impl(bars, resolution)
    result['draw_waves'] = _compute_draw_waves(bars, resolution)
    return result


def _detect_impl(bars: list[dict], resolution: str = '60') -> dict:
    """Detect the dominant Market Structure event (BOS / CHOCH) and Price Channel."""
    if not bars:
        return {**_NO_PATTERN}

    zz_depth    = _TF_ZZ_DEPTH.get(resolution, _ZZ_DEPTH_DEFAULT)
    zz_backstep = _TF_ZZ_BACKSTEP.get(resolution, _ZZ_BACKSTEP_DEFAULT)
    atr_val     = _calc_atr(bars[-50:])
    min_move    = atr_val * _TF_MIN_ATR_MULT.get(resolution, _ATR_MULT_DEFAULT)
    min_bars    = _TF_MIN_BAR_GAP.get(resolution, _BAR_GAP_DEFAULT)
    pivots      = _filter_noise(_clean_pivots(_zigzag_mt5(bars, zz_depth, zz_backstep)), min_move, min_bars)

    n = len(pivots)
    if n < 4:
        return {**_NO_PATTERN, 'pivots_count': n}

    cur_close = bars[-1]['close']
    last_idx  = len(bars) - 1

    classified = _classify_swings(pivots)
    trend      = _detect_trend(classified)
    event, bos_pivot, break_strength = _find_bos_choch(classified, cur_close, trend)
    channel    = _build_channel(classified, bars, cur_close, last_idx, resolution, atr_val)
    channel['atr'] = round(atr_val, 5)   # lộ ATR cho router xét buffer phá biên (mục D)
    wedge      = _detect_wedge(classified, bars)

    # ── Pattern + direction ────────────────────────────────────────────────────
    is_bos   = event.startswith('bos_')
    is_choch = event.startswith('choch_')

    if event in ('bos_bullish', 'choch_bullish'):
        direction = 'bullish'; prediction = 'up'
    elif event in ('bos_bearish', 'choch_bearish'):
        direction = 'bearish'; prediction = 'down'
    else:
        if channel['direction'] == 'up' and channel['pos'] < 0.30:
            direction = 'bullish'; prediction = 'up';   event = 'channel_support'
        elif channel['direction'] == 'down' and channel['pos'] > 0.70:
            direction = 'bearish'; prediction = 'down'; event = 'channel_resistance'
        elif channel['direction'] == 'flat' and channel['pos'] < 0.25:
            direction = 'bullish'; prediction = 'up';   event = 'range_low'
        elif channel['direction'] == 'flat' and channel['pos'] > 0.75:
            direction = 'bearish'; prediction = 'down'; event = 'range_high'
        else:
            waves_out = [{'label': p['label'], 'time': p['time'], 'price': p['price'],
                          'type': p['type'], 'idx': p['idx']} for p in classified[-28:]]
            return {**_NO_PATTERN, 'pivots_count': n,
                    'channel': channel, 'waves': waves_out, 'wedge': wedge,
                    'structure': {'trend': trend, 'bos_level': None,
                                  'event': 'mid_channel', 'swings': classified[-6:]},
                    'rule_signal': _rule_signal(classified, channel)}

    pattern_name = f"{direction}_{event}" if not event.startswith(direction[:4]) else event

    # ── Confidence ─────────────────────────────────────────────────────────────
    recent_labels = [p['label'] for p in classified[-8:]]
    if direction == 'bullish':
        struct_score = (recent_labels.count('HH') * 0.35
                      + recent_labels.count('HL') * 0.25) / max(len(recent_labels) / 2, 1)
    else:
        struct_score = (recent_labels.count('LH') * 0.35
                      + recent_labels.count('LL') * 0.25) / max(len(recent_labels) / 2, 1)
    struct_score = min(struct_score, 1.0)

    break_score  = break_strength if (is_bos or is_choch) else 0.3
    ch_score     = max(channel['quality'], 0.30)

    last_piv_idx = max(p['idx'] for p in classified[-4:]) if classified else 0
    horizon      = _TF_SCALP_HORIZON.get(resolution, _HORIZON_DEFAULT)
    recency_bars = last_idx - last_piv_idx
    recency_f    = max(0.0, 1.0 - recency_bars / horizon)

    choch_penalty = 0.75 if is_choch else 1.0

    confidence = (
        0.35 * struct_score
      + 0.30 * break_score
      + 0.20 * ch_score
      + 0.15 * recency_f
    ) * choch_penalty
    confidence = round(min(confidence, 1.0), 4)

    if confidence < 0.08:
        waves_out_fb = [{'label': p['label'], 'time': p['time'], 'price': p['price'],
                         'type': p['type'], 'idx': p['idx']} for p in classified[-28:]]
        return {**_NO_PATTERN, 'pivots_count': n, 'waves': waves_out_fb,
                'channel': channel, 'wedge': wedge,
                'structure': {'trend': trend, 'bos_level': None,
                              'event': 'mid_channel', 'swings': classified[-6:]},
                'rule_signal': _rule_signal(classified, channel)}

    # ── Targets ────────────────────────────────────────────────────────────────
    highs = sorted([p for p in classified if p['type'] == 'high'], key=lambda x: x['price'])
    lows  = sorted([p for p in classified if p['type'] == 'low'],  key=lambda x: x['price'])

    targets = []
    if prediction == 'up':
        above_highs = [p['price'] for p in highs if p['price'] > cur_close]
        t1 = min(above_highs) if above_highs else channel['upper']
        targets.append({'ratio': 1.0, 'price': round(t1, 5)})
        targets.append({'ratio': 0.5, 'price': round(channel['mid'], 5)})
        if channel['upper'] > t1:
            targets.append({'ratio': 1.5, 'price': round(channel['upper'], 5)})
    else:
        below_lows = [p['price'] for p in lows if p['price'] < cur_close]
        t1 = max(below_lows) if below_lows else channel['lower']
        targets.append({'ratio': 1.0, 'price': round(t1, 5)})
        targets.append({'ratio': 0.5, 'price': round(channel['mid'], 5)})
        if channel['lower'] < t1:
            targets.append({'ratio': 1.5, 'price': round(channel['lower'], 5)})

    next_target = targets[0]['price'] if targets else None

    # ── Waves (for chart drawing) ──────────────────────────────────────────────
    wave_pts   = classified[-28:]
    waves_out2: list[dict] = []
    for p in wave_pts:
        w = {'label': p['label'], 'time': p['time'],
             'price': p['price'], 'type': p['type'], 'idx': p['idx']}
        if bos_pivot and p['idx'] == bos_pivot['idx']:
            w['label'] = 'BOS' if is_bos else 'CHOCH'
        waves_out2.append(w)

    rule_sig = _rule_signal(classified, channel)

    # ── Return ─────────────────────────────────────────────────────────────────
    return {
        'pattern':      pattern_name,
        'confidence':   confidence,
        'shape_score':  round(struct_score, 4),
        'significance': round((break_score + ch_score) / 2, 4),
        'prediction':   prediction,
        'complete':     is_bos,
        'direction':    direction,
        'next_target':  next_target,
        'targets':      targets,
        'fib':          None,
        'channel':      channel,
        'wedge':        wedge,
        'structure': {
            'trend':     trend,
            'bos_level': bos_pivot['price'] if bos_pivot else None,
            'event':     event,
            'swings':    [{'label': p['label'], 'price': p['price']} for p in classified[-6:]],
        },
        'waves':        waves_out2,
        'pivots_count': n,
        'recency':      recency_bars,
        'scalp':        recency_bars <= horizon,
        'computed_at':  int(_time.time() * 1000),
        'rule_signal':  rule_sig,
    }
