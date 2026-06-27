"""Pure technical indicators + fresh price-action helpers feeding the AI prompt."""
import json

import redis.asyncio as aioredis

# Minimum price change (%) in last N bars to flag speed surge
_SPEED_THRESHOLD: dict[str, float] = {
    '1': 0.25, '3': 0.35, '5': 0.45, '15': 0.55, '60': 0.75
}


def _detect_momentum(bars: list[dict], resolution: str) -> dict:
    """Detect if recent price action is an impulsive momentum surge."""
    if len(bars) < 22:
        return {'level': 'LOW', 'direction': None}

    # ATR-20
    true_ranges: list[float] = []
    for i in range(1, 21):
        b, p = bars[-i], bars[-(i + 1)]
        true_ranges.append(max(
            b['high'] - b['low'],
            abs(b['high'] - p['close']),
            abs(b['low']  - p['close']),
        ))
    atr = sum(true_ranges) / len(true_ranges) if true_ranges else 0

    last = bars[-1]
    rng  = last['high'] - last['low']
    body = abs(last['close'] - last['open'])

    body_ratio = body / rng if rng > 0 else 0          # > 0.65 = impulse body
    bar_ratio  = rng / atr  if atr > 0 else 0          # > 1.4  = oversized bar

    # Speed: price change % over last 10 bars
    speed_n   = min(10, len(bars) - 1)
    ref_close = bars[-(speed_n + 1)]['close']
    pct_change = (bars[-1]['close'] - ref_close) / ref_close * 100 if ref_close else 0

    direction = 'up' if pct_change >= 0 else 'down'
    threshold = _SPEED_THRESHOLD.get(resolution, 0.4)

    is_impulse_bar = body_ratio > 0.65 and bar_ratio > 1.4
    is_fast_move   = abs(pct_change) > threshold

    if is_impulse_bar and is_fast_move:
        level = 'HIGH'
    elif is_impulse_bar or is_fast_move:
        level = 'MEDIUM'
    else:
        level = 'LOW'

    return {
        'level':      level,
        'direction':  direction if level != 'LOW' else None,
        'body_ratio': round(body_ratio, 2),
        'bar_ratio':  round(bar_ratio,  2),
        'pct_change': round(pct_change, 3),
        'atr':        round(atr, 5),
    }


# Net % move over the last N bars that qualifies as an active directional break.
_DRIFT_BIAS_THRESHOLD = {'1': 0.15, '3': 0.20, '5': 0.25, '15': 0.35, '60': 0.50}


def _trend_bias(bars: list[dict], resolution: str, n: int = 10) -> dict:
    """Fresh price-action direction, INDEPENDENT of the (lagging) ZigZag detector.
    Flags an active breakdown ('down') / breakout ('up') so the AI does not buy a
    falling knife: price both dropped a meaningful % over the window AND the last
    bar prints a new window low (or high). Otherwise 'flat'."""
    if len(bars) < n + 2:
        return {'bias': 'flat', 'net': 0.0}
    window    = bars[-n:]
    last      = bars[-1]
    ref_close = bars[-(n + 1)]['close']
    net = (last['close'] - ref_close) / ref_close * 100 if ref_close else 0.0
    lo  = min(b['low']  for b in window)
    hi  = max(b['high'] for b in window)
    thr = _DRIFT_BIAS_THRESHOLD.get(resolution, 0.25)
    if net <= -thr and last['low'] <= lo * 1.0001:
        return {'bias': 'down', 'net': round(net, 3), 'level': round(lo, 5)}
    if net >= thr and last['high'] >= hi * 0.9999:
        return {'bias': 'up', 'net': round(net, 3), 'level': round(hi, 5)}
    return {'bias': 'flat', 'net': round(net, 3)}


def _recent_action(bars: list[dict], n: int = 12) -> str:
    """A compact summary of the last N candles so the AI sees the FRESH price
    rhythm that the sparse pivot list misses (cheap; targets detector lag)."""
    if len(bars) < n + 1:
        return ""
    window = bars[-n:]
    last   = bars[-1]
    ref    = bars[-(n + 1)]['close']
    net    = (last['close'] - ref) / ref * 100 if ref else 0.0
    downs  = sum(1 for b in window if b['close'] < b['open'])
    ups    = n - downs
    lo     = min(b['low']  for b in window)
    hi     = max(b['high'] for b in window)
    rng    = hi - lo
    cpos   = (last['close'] - lo) / rng * 100 if rng > 0 else 50.0
    where  = 'gần đáy' if cpos < 30 else 'gần đỉnh' if cpos > 70 else 'giữa range'
    # trailing same-colour streak (only meaningful when >= 2)
    sdir   = 'đỏ' if last['close'] < last['open'] else 'xanh'
    streak = 0
    for b in reversed(window):
        if ('đỏ' if b['close'] < b['open'] else 'xanh') == sdir:
            streak += 1
        else:
            break
    streak_txt = f", {streak} nến {sdir} liên tiếp" if streak >= 2 else ""
    flags  = ''
    if last['low']  <= lo * 1.0001: flags += ' ĐÁY MỚI.'
    if last['high'] >= hi * 0.9999: flags += ' ĐỈNH MỚI.'
    return (
        f"Recent action ({n} nến): {net:+.2f}%, {downs} đỏ/{ups} xanh{streak_txt}, "
        f"đóng cửa {cpos:.0f}% range ({where}).{flags}\n"
    )


async def _read_recent_bars(r: "aioredis.Redis", symbol: str, res: str, n: int = 40) -> list[dict]:
    """Read the last ~n closed bars for a resolution from Redis, + the live bar."""
    raw  = await r.zrange(f"mt5:bars:{symbol}:{res}", -n, -1)
    bars = [json.loads(b) for b in raw]
    live = await r.hgetall(f"mt5:live:{symbol}:{res}")
    if live:
        lb = {k: (int(float(v)) if k in ("volume", "time") else float(v)) for k, v in live.items()}
        if not bars or bars[-1].get("time") != lb.get("time", 0):
            bars.append(lb)
    bars.sort(key=lambda x: x["time"])
    return bars


async def _ltf_context(r: "aioredis.Redis", symbol: str, ltf: str = "5") -> str:
    """Compact lower-timeframe rhythm for the HIGHER-timeframe analyses (15m/1H)
    so the slow frame catches a shift early. One line; guidance is in the system
    prompt (the 'LTF' rule), not repeated here."""
    bars = await _read_recent_bars(r, symbol, ltf)
    if len(bars) < 14:
        return ""
    bias = _trend_bias(bars, ltf)
    nhip = {"down": "đang giảm/phá đáy", "up": "đang tăng/phá đỉnh", "flat": "đi ngang"}.get(bias.get("bias"), "đi ngang")
    # Chỉ dùng nhịp giá CƠ HỌC (tươi từ bars) — KHÔNG đọc signal LLM cached của khung
    # nhỏ. Tránh phụ thuộc vòng tròn HTF↔LTF + dữ liệu cũ; chiều xuống đã do top-down lo.
    return f"LTF {ltf}m: nhịp {nhip} ({bias.get('net', 0):+.2f}%/10 nến).\n"


def _ema_last(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period          # SMA seed
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def _atr_last(bars: list[dict], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        trs.append(max(b['high'] - b['low'], abs(b['high'] - p['close']), abs(b['low'] - p['close'])))
    return sum(trs[-period:]) / min(period, len(trs)) if trs else 0.0


_REGIME_LABEL = {'STRONG_UP': 'Tăng mạnh', 'UP': 'Tăng', 'RANGE': 'Đi ngang',
                 'DOWN': 'Giảm', 'STRONG_DOWN': 'Giảm mạnh'}


def _detect_candle_reversal(bars: list[dict]) -> dict | None:
    """Detect shooting star (bearish) or hammer (bullish) on the last 2 closed bars.
    bars[-1] may still be forming, so we check bars[-2] and bars[-3]."""
    if len(bars) < 5:
        return None
    recent_high = max(b['high'] for b in bars[-10:])
    recent_low  = min(b['low']  for b in bars[-10:])
    recent_rng  = recent_high - recent_low if recent_high > recent_low else 1.0

    for bar in (bars[-2], bars[-3]):
        rng = bar['high'] - bar['low']
        if rng < 1e-8:
            continue
        upper_wick  = bar['high'] - max(bar['open'], bar['close'])
        lower_wick  = min(bar['open'], bar['close']) - bar['low']
        body        = abs(bar['close'] - bar['open'])
        upper_ratio = upper_wick / rng
        lower_ratio = lower_wick / rng
        body_ratio  = body / rng

        if upper_ratio >= 0.60 and body_ratio <= 0.35:
            bar_pos = (bar['high'] - recent_low) / recent_rng
            if bar_pos > 0.65:
                return {'type': 'shooting_star', 'upper_ratio': round(upper_ratio, 2),
                        'bar_pos': round(bar_pos, 2), 'high': bar['high']}

        if lower_ratio >= 0.60 and body_ratio <= 0.35:
            bar_pos = (bar['low'] - recent_low) / recent_rng
            if bar_pos < 0.35:
                return {'type': 'hammer', 'lower_ratio': round(lower_ratio, 2),
                        'bar_pos': round(bar_pos, 2), 'low': bar['low']}
    return None


_CH_REJECT_QUALITY_MIN = 0.5   # R² tối thiểu để TIN biên kênh (loại kênh nhiễu)


def _detect_channel_rejection(bars: list[dict], channel: dict | None) -> dict | None:
    """Xác nhận CHẠM-BẬT ở biên kênh (mean-reversion thật, không chỉ 'gần biên').
    Điều kiện: kênh đủ rõ (quality ≥ ngưỡng) VÀ một nến gần đây có wick TEST biên
    (chạm/xuyên) rồi ĐÓNG CỬA quay vào trong kênh (rejection). Kiểm 2 nến đã đóng
    (bars[-2], bars[-3]) — bars[-1] có thể đang hình thành."""
    if not channel or len(bars) < 4:
        return None
    if (channel.get("quality") or 0) < _CH_REJECT_QUALITY_MIN:
        return None
    upper, lower = channel.get("upper"), channel.get("lower")
    if upper is None or lower is None:
        return None
    width = upper - lower
    if width <= 1e-9:
        return None

    for bar in (bars[-2], bars[-3]):
        low, high, close = bar["low"], bar["high"], bar["close"]
        # Bật ĐÁY: wick chạm/xuyên hỗ trợ (≤ lower+10% width) + đóng cửa quay ≥15% vào trong
        if low <= lower + 0.10 * width and close >= lower + 0.15 * width:
            return {"type": "support_bounce", "boundary": round(lower, 5),
                    "quality": channel.get("quality"),
                    "pierce": round(max(0.0, (lower - low) / width), 3)}
        # Đẩy ĐỈNH: wick chạm/xuyên kháng cự (≥ upper-10% width) + đóng cửa quay ≥15% xuống
        if high >= upper - 0.10 * width and close <= upper - 0.15 * width:
            return {"type": "resistance_reject", "boundary": round(upper, 5),
                    "quality": channel.get("quality"),
                    "pierce": round(max(0.0, (high - upper) / width), 3)}
    return None


def _relative_volume(bars: list[dict], n: int = 20) -> dict:
    """Volume nến VỪA ĐÓNG so với trung bình n nến trước (MT5 tick volume).
    ratio ≥ 1.8 = bùng nổ (xác nhận break thật); ≤ 0.6 = cạn (nghi fakeout).
    Bỏ qua nến live đang hình thành (bars[-1]) để ratio không bị hụt giữa nến."""
    closed = bars[:-1] if len(bars) >= 2 else bars
    if len(closed) < n + 1:
        return {}
    last_v = float(closed[-1].get("volume", 0) or 0)
    prior  = [float(b.get("volume", 0) or 0) for b in closed[-(n + 1):-1]]
    avg = sum(prior) / len(prior) if prior else 0.0
    if avg <= 0:
        return {}
    ratio = last_v / avg
    if   ratio >= 1.8: level = "CAO"
    elif ratio <= 0.6: level = "THẤP"
    else:              level = "TB"
    return {"ratio": round(ratio, 2), "level": level, "last": int(last_v), "avg": round(avg, 1)}


def _vwap(bars: list[dict]) -> dict:
    """VWAP neo theo phiên (reset 00:00 UTC) + band ±1σ, từ typical price (H+L+C)/3
    weighted bởi tick volume. Khi phiên hiện tại quá ít nến (khung lớn / vừa qua nửa
    đêm) → fallback rolling 50 nến. Cho AI mốc giá-trị-hợp-lý nội ngày mà scalper bám."""
    if len(bars) < 10:
        return {}
    last_t    = int(bars[-1].get("time", 0) or 0)
    day_start = (last_t // 86_400_000) * 86_400_000          # 00:00 UTC của nến cuối
    session   = [b for b in bars if int(b.get("time", 0) or 0) >= day_start]
    if len(session) < 10:
        session = bars[-50:]                                  # rolling fallback
    cum_pv = cum_v = 0.0
    for b in session:
        tp  = (b["high"] + b["low"] + b["close"]) / 3
        vol = max(float(b.get("volume", 0) or 0), 1.0)
        cum_pv += tp * vol
        cum_v  += vol
    if cum_v <= 0:
        return {}
    vwap = cum_pv / cum_v
    sq = 0.0
    for b in session:
        tp  = (b["high"] + b["low"] + b["close"]) / 3
        vol = max(float(b.get("volume", 0) or 0), 1.0)
        sq += vol * (tp - vwap) ** 2
    sd    = (sq / cum_v) ** 0.5
    price = bars[-1]["close"]
    return {
        "vwap":     round(vwap, 5),
        "upper":    round(vwap + sd, 5),
        "lower":    round(vwap - sd, 5),
        "dist_pct": round((price - vwap) / vwap * 100 if vwap else 0.0, 3),
        "side":     "trên" if price >= vwap else "dưới",
        "bars":     len(session),
    }


def _trend_regime(bars: list[dict], channel: dict | None = None) -> dict:
    """Master trend regime by fusing several envelope/band votes (max ±8):
    EMA20/50 alignment + Donchian(20) breakout + Keltner position + regression
    channel slope. Gives a single directional context even when MS = no pattern."""
    if len(bars) < 55:
        return {'regime': 'RANGE', 'score': 0, 'label': 'Chưa đủ dữ liệu', 'votes': 0}
    closes = [b['close'] for b in bars]
    last   = closes[-1]
    ema20  = _ema_last(closes, 20)
    ema50  = _ema_last(closes, 50)
    atr    = _atr_last(bars, 14)
    v = 0

    # 1. EMA structure
    if ema20 and ema50:
        if   last > ema20 > ema50: v += 2
        elif last > ema20:         v += 1
        elif last < ema20 < ema50: v -= 2
        elif last < ema20:         v -= 1

    # 2. Donchian(20) breakout (exclude the current bar)
    win = bars[-21:-1] if len(bars) > 21 else bars[:-1]
    dhi = max(b['high'] for b in win)
    dlo = min(b['low']  for b in win)
    drng = dhi - dlo
    if   last >= dhi:                              v += 2
    elif drng > 0 and (last - dlo) / drng > 0.8:  v += 1
    elif last <= dlo:                             v -= 2
    elif drng > 0 and (last - dlo) / drng < 0.2:  v -= 1

    # 3. Keltner position (EMA20 ± 2·ATR)
    if ema20 and atr > 0:
        if   last > ema20 + 2 * atr: v += 2
        elif last > ema20:           v += 1
        elif last < ema20 - 2 * atr: v -= 2
        elif last < ema20:           v -= 1

    # 4. Regression channel slope (already computed by the MS detector)
    if channel:
        d  = channel.get('direction')
        sp = abs(channel.get('slope_pct') or 0)
        if   d == 'up':   v += 2 if sp > 0.02 else 1
        elif d == 'down': v -= 2 if sp > 0.02 else 1

    if   v >= 5:  regime = 'STRONG_UP'
    elif v >= 2:  regime = 'UP'
    elif v <= -5: regime = 'STRONG_DOWN'
    elif v <= -2: regime = 'DOWN'
    else:         regime = 'RANGE'
    return {'regime': regime, 'score': max(-100, min(100, round(v / 8 * 100))),
            'label': _REGIME_LABEL[regime], 'votes': v}


def _sr_zones(bars: list[dict], ms: dict | None, max_each: int = 3) -> dict:
    """Gom swing pivot + biên kênh thành VÙNG hỗ trợ/cản (band + độ mạnh), thay cho
    các mức rời rạc. Pivot nằm trong ~0.5×ATR của nhau = cùng một vùng; chạm nhiều
    lần = vùng mạnh. Trả {'supports': [...], 'resistances': [...]} so với giá hiện
    tại, gần-nhất-trước. Mỗi zone: {lo, hi, mid, strength, recency}."""
    if not bars or not ms:
        return {'supports': [], 'resistances': []}
    atr = _atr_last(bars, 14)
    if atr <= 0:
        return {'supports': [], 'resistances': []}
    price    = bars[-1]['close']
    last_idx = len(bars) - 1
    tol      = 0.5 * atr

    # Điểm mức ứng viên: swing pivot (recency theo idx) + biên kênh (mức cấu trúc)
    cands: list[dict] = []
    for w in ms.get('waves', []) or []:
        idx = w.get('idx', last_idx)
        cands.append({'price': w['price'], 'recency': max(0, last_idx - idx)})
    ch = ms.get('channel') or {}
    for key in ('upper', 'lower'):
        if ch.get(key):
            cands.append({'price': ch[key], 'recency': 0})
    if not cands:
        return {'supports': [], 'resistances': []}

    # Cluster theo khoảng cách giá
    cands.sort(key=lambda c: c['price'])
    clusters: list[list[dict]] = [[cands[0]]]
    for c in cands[1:]:
        if c['price'] - clusters[-1][-1]['price'] <= tol:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    zones: list[dict] = []
    for cl in clusters:
        prices = [c['price'] for c in cl]
        lo, hi = min(prices), max(prices)
        if hi - lo < 0.30 * atr:          # cụm mỏng → cho vùng một độ rộng tối thiểu
            mid = (lo + hi) / 2
            lo, hi = mid - 0.15 * atr, mid + 0.15 * atr
        zones.append({
            'lo': round(lo, 5), 'hi': round(hi, 5), 'mid': round((lo + hi) / 2, 5),
            'strength': len(cl), 'recency': min(c['recency'] for c in cl),
        })

    supports    = sorted([z for z in zones if z['hi'] < price],
                         key=lambda z: price - z['mid'])[:max_each]
    resistances = sorted([z for z in zones if z['lo'] > price],
                         key=lambda z: z['mid'] - price)[:max_each]
    return {'supports': supports, 'resistances': resistances}


def _ema25_value(bars: list[dict]) -> float | None:
    """Trả về giá trị EMA25 hiện tại (số), hoặc None nếu không đủ dữ liệu."""
    if len(bars) < 28:
        return None
    closes = [b['close'] for b in bars]
    return _ema_last(closes, 25)


def _ema25_context(bars: list[dict]) -> str:
    """EMA25 dynamic S/R + slope filter — M5 price action context line."""
    if len(bars) < 28:
        return ""
    closes = [b['close'] for b in bars]
    ema25 = _ema_last(closes, 25)
    if ema25 is None:
        return ""

    price = closes[-1]
    ema25_prev = _ema_last(closes[:-3], 25) if len(closes) > 28 else None
    slope_pct  = (ema25 - ema25_prev) / ema25_prev * 100 if ema25_prev else 0.0

    if   slope_pct >  0.015: slope_label = "dốc LÊN"
    elif slope_pct < -0.015: slope_label = "dốc XUỐNG"
    else:                    slope_label = "nằm ngang"

    dist_pct = (price - ema25) / ema25 * 100
    from backend.ai.prompt import _fmt
    if price > ema25:
        pos   = f"GIÁ TRÊN EMA25 (+{dist_pct:.2f}%)"
        guide = "Dynamic support tại EMA25; pullback test EMA25 = cơ hội BUY thuận regime."
    else:
        pos   = f"GIÁ DƯỚI EMA25 ({dist_pct:.2f}%)"
        guide = "Dynamic resistance tại EMA25; hồi test EMA25 = cơ hội SELL thuận regime."

    if "LÊN" in slope_label:
        slope_guide = "SLOPE FILTER: EMA25 dốc lên → ưu tiên BUY; SELL chỉ conviction=LOW."
    elif "XUỐNG" in slope_label:
        slope_guide = "SLOPE FILTER: EMA25 dốc xuống → ưu tiên SELL; BUY chỉ conviction=LOW."
    else:
        slope_guide = "SLOPE FILTER: EMA25 nằm ngang → ranging, hạn chế directional, ưu tiên WAIT."

    return f"EMA25={_fmt(ema25)} ({slope_label}): {pos}. {guide} {slope_guide}\n"


def _h1_key_levels(bars: list[dict]) -> str:
    """EMA50/200 + PDH/PDL + Weekly range cho khung 1H — neo TP vào mức quan trọng.
    Trader lớn canh EMA50/200; PDH/PDL là S/R tự nhiên nhất trong ngày."""
    if len(bars) < 55:
        return ""
    closes = [b['close'] for b in bars]
    price  = closes[-1]
    from backend.ai.prompt import _fmt

    ema50  = _ema_last(closes, 50)
    ema200 = _ema_last(closes, 200) if len(closes) >= 210 else None

    # PDH/PDL: bars[-48:-24] = ~ngày hôm qua (24h)
    yesterday = bars[-48:-24] if len(bars) >= 48 else []
    pdh = max(b['high'] for b in yesterday) if len(yesterday) >= 10 else None
    pdl = min(b['low']  for b in yesterday) if len(yesterday) >= 10 else None

    # Weekly range: ~5 ngày qua (120 bars), bỏ 24h gần nhất
    week_bars = bars[-144:-24] if len(bars) >= 144 else (bars[:-24] if len(bars) >= 48 else [])
    wh = max(b['high'] for b in week_bars) if len(week_bars) >= 20 else None
    wl = min(b['low']  for b in week_bars) if len(week_bars) >= 20 else None

    parts: list[str] = []
    if ema50:  parts.append(f"EMA50={_fmt(ema50)}")
    if ema200: parts.append(f"EMA200={_fmt(ema200)}")
    if pdh:    parts.append(f"PDH={_fmt(pdh)}")
    if pdl:    parts.append(f"PDL={_fmt(pdl)}")
    if wh:     parts.append(f"WH={_fmt(wh)}")
    if wl:     parts.append(f"WL={_fmt(wl)}")
    if not parts:
        return ""

    key_prices = [p for p in [ema50, ema200, pdh, pdl, wh, wl] if p is not None]
    above = sorted(p for p in key_prices if p > price)
    below = sorted((p for p in key_prices if p < price), reverse=True)

    hint = ""
    if above: hint += f"  ↑TP-BUY→{_fmt(above[0])}"
    if below: hint += f"  ↓TP-SELL→{_fmt(below[0])}"

    return "H1-LEVELS: " + "  ".join(parts) + hint + "\n"


def _h1_ema50_value(bars: list[dict]) -> float | None:
    """EMA50 cho 1H — dùng làm TP1 fallback thay EMA25."""
    if len(bars) < 55:
        return None
    return _ema_last([b['close'] for b in bars], 50)


def _session_context(bars: list[dict]) -> str:
    """Detect London/NY/Asian session from last bar's UTC timestamp."""
    if not bars:
        return ""
    last_ms  = int(bars[-1].get('time', 0) or 0)
    hour_utc = (last_ms // 3_600_000) % 24

    if 13 <= hour_utc < 16:
        return (
            f"SESSION: LONDON+NY OVERLAP (UTC {hour_utc:02d}:xx) ⭐ — volume cao nhất, "
            f"tín hiệu đáng tin nhất. Ưu tiên vào lệnh khi có setup rõ.\n"
        )
    if 7 <= hour_utc < 13:
        return (
            f"SESSION: LONDON (UTC {hour_utc:02d}:xx) — volume tốt, trend rõ. "
            f"Tín hiệu thuận trend London đáng tin.\n"
        )
    if 16 <= hour_utc < 22:
        return (
            f"SESSION: NEW YORK (UTC {hour_utc:02d}:xx) — volume tốt, nhiều volatile moves. "
            f"Cẩn thận news US.\n"
        )
    return (
        f"SESSION: ASIAN (UTC {hour_utc:02d}:xx) ⚠ — volume thấp, sideway nhiều. "
        f"XAUUSD/USOIL ít biến động — hạ conviction mọi tín hiệu, ưu tiên WAIT.\n"
    )


def _detect_sr_probe(bars: list[dict], sr_zones: dict | None,
                     atr: float, tol_mult: float = 0.10) -> dict | None:
    """Giá vừa THỌC vào (hoặc sát trong tolerance) VÙNG S/R gần nhất rồi phản ứng —
    cái mà `_detect_channel_rejection` bỏ sót (nó chỉ canh biên KÊNH hồi quy, lại cần
    quality≥0.5 và bỏ qua nến live). Tolerance = tol_mult×ATR để cú wick hụt vài điểm
    vẫn tính là 'đã test'. Quét 3 nến gần nhất KỂ CẢ nến live, lấy probe gần nhất.

    reacted=True nghĩa đã ĐÓNG CỬA quay khỏi vùng (rejection ở cản / bounce ở hỗ trợ)
    = xác nhận mạnh; reacted=False = đang test/giữ ở vùng, chưa quay đầu rõ."""
    if not sr_zones or atr <= 0:
        return None
    tol = tol_mult * atr
    rz  = (sr_zones.get("resistances") or [None])[0]
    sz  = (sr_zones.get("supports")    or [None])[0]
    window = bars[-3:] if len(bars) >= 3 else bars
    for off, bar in enumerate(reversed(window)):     # off=0 = nến mới nhất (có thể live)
        high, low, close = bar["high"], bar["low"], bar["close"]
        if rz and (rz["lo"] - tol) <= high <= (rz["hi"] + tol):
            return {"side": "resistance", "zone_lo": rz["lo"], "zone_hi": rz["hi"],
                    "wick": round(high, 5), "reacted": close < rz["lo"],
                    "strength": rz.get("strength", 1), "bars_ago": off}
        if sz and (sz["lo"] - tol) <= low <= (sz["hi"] + tol):
            return {"side": "support", "zone_lo": sz["lo"], "zone_hi": sz["hi"],
                    "wick": round(low, 5), "reacted": close > sz["hi"],
                    "strength": sz.get("strength", 1), "bars_ago": off}
    return None
