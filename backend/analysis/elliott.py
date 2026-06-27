"""
Elliott Wave detector — impulse (1-2-3-4-5) + corrective (A-B-C).

Algorithm:
  1. ZigZag: ratcheting pivot-high / pivot-low finder with adaptive deviation%.
  2. Slide windows over the recent pivots:
        - 6-pivot windows → 5-wave impulse (validated against the 3 hard EW rules)
        - 4-pivot windows → A-B-C correction (+ in-progress C from the live close)
  3. Score each candidate's SHAPE via Fibonacci relationships (0..1).
  4. Weight by SIGNIFICANCE (amplitude vs the data range + recency) so a large,
     meaningful structure beats a tiny but Fib-perfect wiggle.
  5. confidence = shape_score × significance  (honest, not just shape fit).
  6. Return the best match with wave points, prediction and confidence.
"""
from __future__ import annotations

import time as _time

# ── Constants ──────────────────────────────────────────────────────────────────

# ZigZag deviation% per timeframe — tuned for scalping (researched on BTCUSD):
# lower dev = finer micro-swings = fresher, more-actionable signals on low TFs.
_TF_DEV: dict[str, float] = {
    '1': 0.05, '3': 0.22, '5': 0.25, '15': 0.25, '60': 0.80,
}
_DEV_DEFAULT = 0.30

# A signal is "scalp-actionable" only if its last wave is within this many bars of
# the current bar (i.e. the setup is at the right edge, not a stale historical one).
_TF_SCALP_HORIZON: dict[str, int] = {
    '1': 12, '3': 12, '5': 10, '15': 8, '60': 6,
}
_HORIZON_DEFAULT = 10

_FIB_B_GOLDEN = (0.50, 0.618)
_FIB_B_WIDE   = (0.382, 0.786)
_FIB_C_LEVELS = [0.618, 1.0, 1.272, 1.618]
_FIB_C_TOL    = 0.12


# ── ZigZag ─────────────────────────────────────────────────────────────────────

def _zigzag(bars: list[dict], dev: float) -> list[dict]:
    """
    Ratcheting ZigZag.  Returns alternating pivot dicts:
      {time, price, type: 'high'|'low', idx}
    Bar `time` values are passed through unchanged (milliseconds).
    """
    if len(bars) < 5:
        return []

    pivots: list[dict] = []
    trend: int | None = None   # +1 tracking up toward peak, -1 toward trough

    peak       = bars[0]['high']
    peak_idx   = 0
    trough     = bars[0]['low']
    trough_idx = 0

    for i, bar in enumerate(bars):
        h, l = bar['high'], bar['low']

        if trend is None:
            if h > peak:
                peak, peak_idx = h, i
            if l < trough:
                trough, trough_idx = l, i

            # Down break from current peak
            if peak > 0 and (peak - l) / peak * 100 >= dev:
                pivots.append({'time': bars[peak_idx]['time'], 'price': peak,
                               'type': 'high', 'idx': peak_idx})
                trend, trough, trough_idx = -1, l, i

            # Up break from current trough (only if still no direction set)
            elif trough > 0 and (h - trough) / trough * 100 >= dev:
                pivots.append({'time': bars[trough_idx]['time'], 'price': trough,
                               'type': 'low', 'idx': trough_idx})
                trend, peak, peak_idx = +1, h, i

        elif trend == +1:
            if h >= peak:
                peak, peak_idx = h, i
            elif peak > 0 and (peak - l) / peak * 100 >= dev:
                pivots.append({'time': bars[peak_idx]['time'], 'price': peak,
                               'type': 'high', 'idx': peak_idx})
                trend, trough, trough_idx = -1, l, i

        else:  # trend == -1
            if l <= trough:
                trough, trough_idx = l, i
            elif trough > 0 and (h - trough) / trough * 100 >= dev:
                pivots.append({'time': bars[trough_idx]['time'], 'price': trough,
                               'type': 'low', 'idx': trough_idx})
                trend, peak, peak_idx = +1, h, i

    # Append the current unconfirmed extremum as the last (provisional) pivot
    if trend == +1 and (not pivots or pivots[-1]['idx'] != peak_idx):
        pivots.append({'time': bars[peak_idx]['time'], 'price': peak,
                       'type': 'high', 'idx': peak_idx})
    elif trend == -1 and (not pivots or pivots[-1]['idx'] != trough_idx):
        pivots.append({'time': bars[trough_idx]['time'], 'price': trough,
                       'type': 'low', 'idx': trough_idx})

    return pivots


# ── Significance (amplitude + recency) ─────────────────────────────────────────

def _significance(amp: float, data_span: float, end_idx: int,
                  last_idx: int, total_bars: int) -> float:
    """
    How much a pattern *matters*, 0..1.

    - amplitude: pattern height vs the whole data window's price range — a large
      swing scores near 1, a tiny wiggle near 0.
    - recency: patterns that end close to the latest bar are weighted higher.

    This is what stops the detector from latching onto a small but Fib-perfect
    zigzag while ignoring the dominant structure.
    """
    amp_frac = min(amp / data_span, 1.0) if data_span > 0 else 0.0
    recency  = max(0.0, 1.0 - (last_idx - end_idx) / max(total_bars, 1))
    return min(1.0, (0.6 * amp_frac + 0.4) * (0.6 + 0.4 * recency))


# ── Scoring: A-B-C ─────────────────────────────────────────────────────────────

def _score_abc(a_size: float, b_size: float, c_size: float) -> float:
    """Return A-B-C shape quality 0.0–1.0 (Fibonacci fit only)."""
    if a_size <= 0:
        return 0.0

    score = 0.0

    b_retrace = b_size / a_size
    if _FIB_B_GOLDEN[0] <= b_retrace <= _FIB_B_GOLDEN[1]:
        score += 0.40
    elif _FIB_B_WIDE[0] <= b_retrace <= _FIB_B_WIDE[1]:
        score += 0.22

    c_ratio   = c_size / a_size
    best_dist = min(abs(c_ratio - lvl) for lvl in _FIB_C_LEVELS)
    if best_dist <= _FIB_C_TOL:
        score += 0.40 * (1.0 - best_dist / _FIB_C_TOL)
    elif best_dist <= _FIB_C_TOL * 2.5:
        score += 0.15 * (1.0 - best_dist / (_FIB_C_TOL * 2.5))

    sym    = 1.0 - min(abs(c_size - a_size) / a_size, 1.0)
    score += sym * 0.20

    return min(score, 1.0)


def _fib_targets(origin_price: float, a_end_price: float,
                 b_end_price: float, direction: int) -> list[dict]:
    """Fibonacci C-wave projection targets from the B endpoint."""
    a_size = abs(a_end_price - origin_price)
    return [
        {'ratio': ratio, 'price': round(b_end_price + direction * a_size * ratio, 5)}
        for ratio in [0.618, 1.0, 1.272, 1.618]
    ]


# ── Scoring: impulse 1-2-3-4-5 ─────────────────────────────────────────────────

def _score_impulse(w1: float, w2: float, w3: float,
                   w4: float, w5: float) -> float:
    """Return 5-wave impulse shape quality 0.0–1.0 (Fibonacci fit only)."""
    if w1 <= 0:
        return 0.0

    score = 0.0

    # Wave 2 retraces ~0.5–0.618 of wave 1
    r2 = w2 / w1
    if 0.5 <= r2 <= 0.618:
        score += 0.22
    elif 0.382 <= r2 <= 0.786:
        score += 0.12

    # Wave 3 extends wave 1 (ideally ≥1.618), and is usually the strongest leg
    r3 = w3 / w1
    if r3 >= 1.5:
        score += 0.30
    elif r3 >= 1.0:
        score += 0.20
    elif r3 >= 0.618:
        score += 0.08

    # Wave 4 retraces ~0.236–0.382 of wave 3
    if w3 > 0:
        r4 = w4 / w3
        if 0.236 <= r4 <= 0.382:
            score += 0.18
        elif 0.146 <= r4 <= 0.5:
            score += 0.10

    # Wave 5 relates to wave 1 (≈0.618 / 1.0 / 1.618)
    r5 = w5 / w1
    best5 = min(abs(r5 - lvl) for lvl in (0.618, 1.0, 1.618))
    if best5 <= 0.15:
        score += 0.18 * (1.0 - best5 / 0.15)
    elif best5 <= 0.40:
        score += 0.08 * (1.0 - best5 / 0.40)

    # Bonus: wave 3 is the longest of 1/3/5 (textbook impulse)
    if w3 >= w1 and w3 >= w5:
        score += 0.12

    return min(score, 1.0)


def _impulse_targets(p0_price: float, p5_price: float, direction: str) -> list[dict]:
    """Expected correction retracement levels after a completed 5-wave impulse."""
    move = abs(p5_price - p0_price)
    d    = -1 if direction == 'bullish' else +1   # correction is opposite
    return [
        {'ratio': ratio, 'price': round(p5_price + d * move * ratio, 5)}
        for ratio in [0.382, 0.5, 0.618]
    ]


# ── Candidate builders ─────────────────────────────────────────────────────────

def _try_impulse(ps: list[dict], data_span: float, last_idx: int,
                 total_bars: int, *, complete: bool = True,
                 penalty: float = 1.0) -> dict | None:
    """Validate a 6-pivot window as a 5-wave impulse → candidate dict or None."""
    if len(ps) < 6:
        return None
    p0, p1, p2, p3, p4, p5 = ps[:6]
    types = [p['type'] for p in ps[:6]]

    if types == ['low', 'high', 'low', 'high', 'low', 'high']:
        direction = 'bullish'
        w1 = p1['price'] - p0['price']
        w2 = p1['price'] - p2['price']
        w3 = p3['price'] - p2['price']
        w4 = p3['price'] - p4['price']
        w5 = p5['price'] - p4['price']
        ok = (w1 > 0 and w3 > 0 and w5 > 0
              and p2['price'] > p0['price']    # rule 1: w2 doesn't break w1 start
              and p3['price'] > p1['price']    # w3 makes a new high
              and p4['price'] > p1['price']    # rule 3: w4 doesn't overlap w1
              and p5['price'] > p3['price'])   # w5 makes a new high
    elif types == ['high', 'low', 'high', 'low', 'high', 'low']:
        direction = 'bearish'
        w1 = p0['price'] - p1['price']
        w2 = p2['price'] - p1['price']
        w3 = p2['price'] - p3['price']
        w4 = p4['price'] - p3['price']
        w5 = p4['price'] - p5['price']
        ok = (w1 > 0 and w3 > 0 and w5 > 0
              and p2['price'] < p0['price']
              and p3['price'] < p1['price']
              and p4['price'] < p1['price']
              and p5['price'] < p3['price'])
    else:
        return None

    if not ok:
        return None
    # rule 2: wave 3 is never the shortest of 1/3/5
    if w3 < w1 and w3 < w5:
        return None

    shape = _score_impulse(w1, w2, w3, w4, w5)
    amp   = abs(p5['price'] - p0['price'])
    sig   = _significance(amp, data_span, p5['idx'], last_idx, total_bars)

    targets = _impulse_targets(p0['price'], p5['price'], direction)
    # After 5 waves, expect a correction in the opposite direction
    prediction = 'down' if direction == 'bullish' else 'up'
    nt = None
    for t in targets:
        if prediction == 'down' and t['price'] < p5['price']:
            nt = t['price']; break
        if prediction == 'up' and t['price'] > p5['price']:
            nt = t['price']; break

    return {
        'pattern': f'{direction}_impulse', 'direction': direction, 'complete': complete,
        'shape_score': round(shape, 4), 'significance': round(sig, 4),
        'confidence': shape * sig * penalty,
        'prediction': prediction, 'next_target': nt, 'targets': targets,
        'fib': {
            'w2_w1': round(w2 / w1, 3), 'w3_w1': round(w3 / w1, 3),
            'w4_w3': round(w4 / w3, 3) if w3 > 0 else None,
            'w5_w1': round(w5 / w1, 3),
        },
        'waves': [
            {'label': '0', **p0}, {'label': '1', **p1}, {'label': '2', **p2},
            {'label': '3', **p3}, {'label': '4', **p4}, {'label': '5', **p5},
        ],
    }


def _try_abc(ps: list[dict], data_span: float, last_idx: int,
             total_bars: int, *, complete: bool = True,
             penalty: float = 1.0) -> dict | None:
    """Validate a 4-pivot window as an A-B-C correction → candidate dict or None."""
    if len(ps) < 4:
        return None
    p0, p1, p2, p3 = ps[:4]

    if (p0['type'] == 'high' and p1['type'] == 'low'
            and p2['type'] == 'high' and p3['type'] == 'low'):
        direction, d = 'bearish', -1
        a = p0['price'] - p1['price']
        b = p2['price'] - p1['price']
        c = p2['price'] - p3['price']
        ok = a > 0 and b > 0 and c > 0 and b < a and p2['price'] < p0['price']
    elif (p0['type'] == 'low' and p1['type'] == 'high'
          and p2['type'] == 'low' and p3['type'] == 'high'):
        direction, d = 'bullish', +1
        a = p1['price'] - p0['price']
        b = p1['price'] - p2['price']
        c = p3['price'] - p2['price']
        ok = a > 0 and b > 0 and c > 0 and b < a and p2['price'] > p0['price']
    else:
        return None

    if not ok:
        return None

    shape  = _score_abc(a, b, c)
    prices = [p['price'] for p in (p0, p1, p2, p3)]
    amp    = max(prices) - min(prices)
    sig    = _significance(amp, data_span, p3['idx'], last_idx, total_bars)

    targets = _fib_targets(p0['price'], p1['price'], p2['price'], d)
    # Complete ABC → reversal (opposite impulse); C forming → continuation
    if complete:
        prediction = 'up' if direction == 'bearish' else 'down'
    else:
        prediction = 'down' if direction == 'bearish' else 'up'
    # next_target = first Fib level the C leg could still reach (continuation dir)
    nt = None
    for t in targets:
        if d < 0 and t['price'] < p3['price']:
            nt = t['price']; break
        if d > 0 and t['price'] > p3['price']:
            nt = t['price']; break

    return {
        'pattern': f'{direction}_abc', 'direction': direction, 'complete': complete,
        'shape_score': round(shape, 4), 'significance': round(sig, 4),
        'confidence': shape * sig * penalty,
        'prediction': prediction, 'next_target': nt, 'targets': targets,
        'fib': {'b_retrace': round(b / a, 4), 'c_to_a': round(c / a, 4)},
        'waves': [
            {'label': 'Origin', **p0}, {'label': 'A', **p1},
            {'label': 'B', **p2}, {'label': 'C', **p3},
        ],
    }


# ── Main detector ──────────────────────────────────────────────────────────────

_NO_PATTERN: dict = {
    'pattern': 'none',
    'confidence': 0.0,
    'shape_score': 0.0,
    'significance': 0.0,
    'prediction': 'neutral',
    'complete': False,
    'direction': None,
    'next_target': None,
    'targets': [],
    'fib': None,
    'waves': [],
    'pivots_count': 0,
    'recency': None,
    'scalp': False,
}


def detect(bars: list[dict], resolution: str = '60') -> dict:
    """
    Detect the best Elliott structure (5-wave impulse or A-B-C) in `bars`.

    Each bar must have keys: time (ms), open, high, low, close.
    Returns a result dict ready for JSON serialisation. `confidence` already
    blends Fibonacci shape with the pattern's significance (amplitude + recency);
    `shape_score` and `significance` are exposed separately for transparency.
    """
    if not bars:
        return {**_NO_PATTERN}

    dev    = _TF_DEV.get(resolution, _DEV_DEFAULT)
    pivots = _zigzag(bars, dev)
    n      = len(pivots)
    if n < 4:
        return {**_NO_PATTERN, 'pivots_count': n}

    hi        = max(b['high'] for b in bars)
    lo        = min(b['low']  for b in bars)
    data_span = max(hi - lo, 1e-9)
    last_idx  = len(bars) - 1
    total     = len(bars)

    candidates: list[dict] = []

    # ── 5-wave impulse (6-pivot windows) ─────────────────────────────────────
    for i in range(max(0, n - 20), n - 5):
        cand = _try_impulse(pivots[i:i + 6], data_span, last_idx, total)
        if cand:
            candidates.append(cand)

    # ── In-progress impulse: 4 confirmed pivots + live close as wave-5 ────────
    if n >= 5:
        for i in range(max(0, n - 16), n - 4):
            ps = pivots[i:i + 5]
            p0, p1, p2, p3, p4 = ps
            cur_close = bars[-1]['close']
            cur_time  = bars[-1]['time']
            # Bullish in-progress: low-high-low-high-low, w5 forming above p4
            if ([p['type'] for p in ps] == ['low', 'high', 'low', 'high', 'low']
                    and cur_close > p4['price']):
                p5_live = {'time': cur_time, 'price': cur_close, 'type': 'high', 'idx': last_idx}
                cand = _try_impulse([p0, p1, p2, p3, p4, p5_live], data_span, last_idx, total,
                                    complete=False, penalty=0.80)
                if cand:
                    candidates.append(cand)
            # Bearish in-progress: high-low-high-low-high, w5 forming below p4
            elif ([p['type'] for p in ps] == ['high', 'low', 'high', 'low', 'high']
                    and cur_close < p4['price']):
                p5_live = {'time': cur_time, 'price': cur_close, 'type': 'low', 'idx': last_idx}
                cand = _try_impulse([p0, p1, p2, p3, p4, p5_live], data_span, last_idx, total,
                                    complete=False, penalty=0.80)
                if cand:
                    candidates.append(cand)

    # ── A-B-C correction (4-pivot windows) ────────────────────────────────────
    for i in range(max(0, n - 16), n - 3):
        cand = _try_abc(pivots[i:i + 4], data_span, last_idx, total)
        if cand:
            candidates.append(cand)

    # ── In-progress C (3 confirmed pivots + live close) ────────────────────────
    if n >= 3:
        p0, p1, p2 = pivots[-3], pivots[-2], pivots[-1]
        cur_close  = bars[-1]['close']
        cur_time   = bars[-1]['time']

        if (p0['type'] == 'high' and p1['type'] == 'low'
                and p2['type'] == 'high' and cur_close < p2['price']):
            p3_live = {'time': cur_time, 'price': cur_close, 'type': 'low', 'idx': last_idx}
            cand = _try_abc([p0, p1, p2, p3_live], data_span, last_idx, total,
                            complete=False, penalty=0.65)
            if cand:
                candidates.append(cand)
        elif (p0['type'] == 'low' and p1['type'] == 'high'
              and p2['type'] == 'low' and cur_close > p2['price']):
            p3_live = {'time': cur_time, 'price': cur_close, 'type': 'high', 'idx': last_idx}
            cand = _try_abc([p0, p1, p2, p3_live], data_span, last_idx, total,
                            complete=False, penalty=0.65)
            if cand:
                candidates.append(cand)

    if not candidates:
        return {**_NO_PATTERN, 'pivots_count': n}

    # Selection is recency-biased (for scalping): keep respecting confidence, but
    # reward setups whose last wave sits near the right edge so the pick stays
    # actionable instead of latching onto a stale historical pattern.
    horizon = _TF_SCALP_HORIZON.get(resolution, _HORIZON_DEFAULT)

    def _recency(c: dict) -> int:
        return last_idx - max(w['idx'] for w in c['waves'])

    def _sel_score(c: dict) -> float:
        rf = max(0.0, 1.0 - _recency(c) / horizon)
        return c['confidence'] * (0.4 + 0.6 * rf)

    best = max(candidates, key=_sel_score)
    if best['confidence'] < 0.05:
        return {**_NO_PATTERN, 'pivots_count': n}

    recency = _recency(best)

    return {
        'pattern':      best['pattern'],
        'confidence':   round(best['confidence'], 4),
        'shape_score':  best['shape_score'],
        'significance': best['significance'],
        'prediction':   best['prediction'],
        'complete':     best['complete'],
        'direction':    best['direction'],
        'next_target':  best['next_target'],
        'targets':      best['targets'],
        'fib':          best['fib'],
        'waves':        best['waves'],
        'pivots_count': n,
        'recency':      recency,                 # bars from last wave to current
        'scalp':        recency <= horizon,      # actionable for a scalp entry now
        'computed_at':  int(_time.time() * 1000),
    }
