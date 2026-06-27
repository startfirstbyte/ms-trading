"""AI analysis core — compute MS context, call Claude, post-process guards, persist."""
import asyncio
import contextlib
import json
import logging
import time

import redis.asyncio as aioredis

from backend.analysis.market_structure import detect as ms_detect
from backend.core import config, state
from backend.db.postgres import _insert_ai_prediction
from backend.positions import (
    _position_outcome, _position_status,
    _sig_side, _ALL_DIRECTIONAL, _LIMIT_SIGNALS, _VALID_SIGNALS,
    _TRIGGER_RISE, _TRIGGER_FALL,
)
from backend.ai.prompt import _AI_SYSTEM, _AI_SCHEMA, _build_ai_user_prompt
from backend.ai.indicators import (
    _detect_momentum, _trend_bias, _recent_action, _ltf_context, _trend_regime,
    _detect_candle_reversal, _detect_channel_rejection, _sr_zones,
    _relative_volume, _vwap, _detect_sr_probe,
    _ema25_context, _ema25_value, _session_context,
    _h1_key_levels, _h1_ema50_value, _ema_last,
)
from backend.ai.winrate import _recent_winrate

log = logging.getLogger(__name__)

_PRICE_DRIFT_THRESHOLD = 0.005   # 0.5 % — re-analyze if price drifted more than this
_PATTERN_STABLE_TTL   = 300      # seconds — max age of cached analysis before forcing refresh
_SL_CD_TTL            = 30 * 60  # 30 min  — conviction cap window after recent SL hit
# A directional BUY/SELL is LOCKED until it hits TP/SL. 30d ceiling = 1m-bar retention.
_AI_LOCK_TTL          = 30 * 24 * 3600


# ── Data fetch ───────────────────────────────────────────────────────────────────

async def _compute_ms_for_ai(r: aioredis.Redis, symbol: str, res: str) -> tuple[dict, list[dict]]:
    n_bars   = 300
    raw      = await r.zrange(f"mt5:bars:{symbol}:{res}", -n_bars, -1)
    bar_list = [json.loads(b) for b in raw]

    # 1m is too noisy — inherit MS from 3m (peaks/troughs, BOS/CHOCH, channel are the same)
    if res == "1":
        cached_3m = await r.get(f"ms:{symbol}:3")
        if cached_3m:
            ms_3m = json.loads(cached_3m)
            ms_3m["symbol"]     = symbol
            ms_3m["resolution"] = "1"
            await r.set(f"ms:{symbol}:1", json.dumps(ms_3m), ex=config._MS_TTL)
            return ms_3m, bar_list

    # PG fallback: if Redis has fewer bars than needed, fetch from DB + refill Redis
    if len(bar_list) < n_bars and state.pg_pool:
        table = config._BAR_TABLE.get(res)
        if table:
            try:
                async with state.pg_pool.acquire() as conn:
                    rows = await conn.fetch(
                        f"SELECT time_ms,open,high,low,close,volume FROM {table} "
                        f"WHERE symbol=$1 ORDER BY time_ms DESC LIMIT 300",
                        symbol
                    )
                pg_bars = [{'time': r2['time_ms'], 'open': r2['open'], 'high': r2['high'],
                            'low': r2['low'], 'close': r2['close'], 'volume': r2['volume']}
                           for r2 in reversed(rows)]
                if pg_bars:
                    for bar in pg_bars:
                        await r.zadd(f"mt5:bars:{symbol}:{res}", {json.dumps(bar): bar['time']})
                    bar_list = pg_bars
                    log.info(f"Refilled Redis {symbol}:{res} with {len(pg_bars)} bars from PG")
            except Exception:
                log.exception(f"PG query for AI MS failed {symbol}:{res}")

    live = await r.hgetall(f"mt5:live:{symbol}:{res}")
    if live:
        live_bar = {
            k: int(float(v)) if k in ("volume", "time") else float(v)
            for k, v in live.items()
        }
        if not bar_list or bar_list[-1]["time"] != live_bar.get("time", 0):
            bar_list.append(live_bar)
    bar_list.sort(key=lambda x: x["time"])

    result = ms_detect(bar_list, res)
    result["symbol"]     = symbol
    result["resolution"] = res
    await r.set(f"ms:{symbol}:{res}", json.dumps(result), ex=config._MS_TTL)
    return result, bar_list


async def _fetch_htf_context(r: aioredis.Redis, symbol: str) -> dict:
    """Cached 15m + 1H AI results — the higher-TF filter for the lower frames."""
    htf_context: dict = {}
    for htf_res in ('15', '60'):
        raw = await r.get(f"ai_analysis:{symbol}:{htf_res}")
        if raw:
            try:
                htf_context[htf_res] = json.loads(raw)
            except Exception:
                pass
    return htf_context


# ── Claude call ──────────────────────────────────────────────────────────────────

async def _call_claude_local(user_prompt: str) -> tuple[str, dict]:
    """Gọi Claude qua CLI Bridge trên host (subscription quota)."""
    import httpx
    async with httpx.AsyncClient(timeout=350) as client:
        resp = await client.post(
            f"{config.LOCAL_CLAUDE_URL}/analyze",
            json={"system": _AI_SYSTEM, "prompt": user_prompt, "model": config.AI_LOCAL_MODEL},
        )
        resp.raise_for_status()
        d = resp.json()
    token_stats = {
        "input_tokens":  d.get("input_tokens", 0),
        "output_tokens": d.get("output_tokens", 0),
        "model":         f"local:{d.get('model', config.AI_LOCAL_MODEL)}",
        "timestamp_ms":  int(time.time() * 1000),
        "estimated":     False,
    }
    return d.get("result", "").strip(), token_stats


async def _call_claude(user_prompt: str) -> tuple[dict, dict]:
    sem = state.ai_semaphore or contextlib.nullcontext()
    async with sem:
        if config.AI_BACKEND == "local":
            raw, token_stats = await _call_claude_local(user_prompt)
        else:
            msg = await state.ai_client.messages.create(
                model=config.AI_MODEL,
                max_tokens=700,
                temperature=0,
                system=_AI_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                output_config={"format": {"type": "json_schema", "schema": _AI_SCHEMA}},
            )
            raw = msg.content[0].text.strip()
            token_stats = {
                "input_tokens":  msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
                "model":         config.AI_MODEL,
                "timestamp_ms":  int(time.time() * 1000),
                "estimated":     False,
            }
    try:
        start  = raw.find("{")
        end    = raw.rfind("}") + 1
        signal = json.loads(raw[start:end]) if start >= 0 and end > start else {}
        if not signal:
            raise ValueError("empty")
    except Exception:
        log.warning(f"Claude JSON parse failed: {raw[:120]!r}")
        signal = {"signal": "WAIT", "conviction": "LOW", "analysis": raw[:120] or "Parse error"}
    return signal, token_stats


# ── Pre-analysis cache guards ────────────────────────────────────────────────────

async def _check_active_lock(r: aioredis.Redis, symbol: str, res: str,
                              quote: dict | None) -> dict | None:
    """Return the cached signal if a directional signal is still active (not TP/SL yet).
    Sets SL cooldown when the prior signal hit SL. Returns None to proceed with new analysis."""
    try:
        locked_raw = await r.get(f"ai_analysis:{symbol}:{res}")
        if not locked_raw:
            return None
        c = json.loads(locked_raw)
        csig, c_entry, c_tp, c_sl = (c.get("signal"), c.get("entry_zone"),
                                      c.get("target"), c.get("stop_loss"))
        if csig not in _ALL_DIRECTIONAL or not (c_entry and c_tp and c_sl):
            return None

        limit_zone = c_entry if csig in _LIMIT_SIGNALS else None
        outcome = await _position_outcome(symbol, res, csig, c.get("timestamp_ms"),
                                          c_sl, c_tp, entry_zone=limit_zone)

        # For market orders: also check current live price (forming bar)
        if outcome in ("open", "not_triggered") and csig not in _LIMIT_SIGNALS:
            live = _position_status({"side": _sig_side(csig), "stop": c_sl, "target": c_tp},
                                    (quote or {}).get("bid"))
            if live != "open":
                outcome = live

        # Stale limit: price moved too far against the unfilled limit order
        if outcome == "not_triggered" and csig in _LIMIT_SIGNALS:
            bid = (quote or {}).get("bid") or 0
            if bid and c_sl:
                sl_dist = abs(c_sl - c_entry)
                if csig in _TRIGGER_RISE and bid < c_entry - sl_dist:
                    log.info(f"AI [{symbol}:{res}] {csig} stale (bid {bid:.2f} < entry {c_entry:.2f} - {sl_dist:.2f}) → mở lock")
                    outcome = "stale"
                elif csig in _TRIGGER_FALL and bid > c_entry + sl_dist:
                    log.info(f"AI [{symbol}:{res}] {csig} stale (bid {bid:.2f} > entry {c_entry:.2f} + {sl_dist:.2f}) → mở lock")
                    outcome = "stale"

        if outcome in ("open", "not_triggered"):
            await r.expire(f"ai_analysis:{symbol}:{res}", _AI_LOCK_TTL)
            log.info(f"AI [{symbol}:{res}] {csig} locked — chưa chạm TP/SL, giữ tín hiệu")
            return c

        log.info(f"AI [{symbol}:{res}] {csig} đã chạm {outcome.upper()} → phân tích lại")
        if outcome == "sl":
            await r.set(f"ai:sl_cd:{symbol}:{res}", "1", ex=_SL_CD_TTL)
            log.info(f"AI [{symbol}:{res}] SL cooldown set ({_SL_CD_TTL//60}min)")
    except Exception:
        pass
    return None


async def _check_stability_gate(r: aioredis.Redis, symbol: str, res: str,
                                 ms: dict, quote: dict | None, momentum: dict) -> dict | None:
    """Return cached analysis when MS pattern + price are stable. None = call Claude."""
    cached_raw = await r.get(f"ai_analysis:{symbol}:{res}")
    if not cached_raw:
        return None
    try:
        cached          = json.loads(cached_raw)
        cached_age      = (time.time() * 1000 - cached.get("timestamp_ms", 0)) / 1000
        same_pattern    = cached.get("ms_pattern") == ms.get("pattern", "none")
        cached_bid      = cached.get("analysis_bid") or 0
        current_bid     = (quote or {}).get("bid") or 0
        price_drift     = abs(current_bid - cached_bid) / cached_bid if cached_bid else 1.0
        momentum_stable = momentum.get('level') != 'HIGH'
        if same_pattern and price_drift < _PRICE_DRIFT_THRESHOLD and cached_age < _PATTERN_STABLE_TTL and momentum_stable:
            log.info(f"AI [{symbol}:{res}] cache HIT — pattern={same_pattern} drift={price_drift:.3%} age={cached_age:.0f}s")
            return cached
    except Exception:
        pass   # corrupted cache → fall through to re-analyze
    return None


# ── Post-analysis signal guards ──────────────────────────────────────────────────

async def _apply_signal_guards(
    r: aioredis.Redis, signal: dict, symbol: str, res: str,
    ms: dict, bias: dict, reversal: dict | None, ch_rejection: dict | None,
    sr_zones: dict, momentum: dict, htf_context: dict | None,
    prev_trade: dict | None, bar_list: list[dict],
) -> dict:
    """Apply all post-processing guards to the raw Claude signal in sequence."""

    # 1. Normalise: reject unknown values; translate 'HOLD' → prior directional signal
    raw_sig = signal.get("signal")
    if raw_sig not in _VALID_SIGNALS:
        prev_sig = (prev_trade or {}).get("signal")
        signal["signal"] = prev_sig if (raw_sig == "HOLD" and prev_sig in _ALL_DIRECTIONAL) else "WAIT"

    # 2. Breakdown/breakout guard — never enter against a fresh impulse move
    if bias.get("bias") == "down" and _sig_side(signal.get("signal")) == "BUY":
        log.info(f"AI [{symbol}:{res}] breakdown guard → {signal['signal']} → WAIT (net {bias.get('net')}%)")
        signal["signal"] = "WAIT"
        signal["conviction"] = "LOW"
        signal["analysis"] = (signal.get("analysis") or "") + " [Đang phá đáy → WAIT]"
    elif bias.get("bias") == "up" and _sig_side(signal.get("signal")) == "SELL":
        log.info(f"AI [{symbol}:{res}] breakout guard → {signal['signal']} → WAIT (net {bias.get('net')}%)")
        signal["signal"] = "WAIT"
        signal["conviction"] = "LOW"
        signal["analysis"] = (signal.get("analysis") or "") + " [Đang phá đỉnh → WAIT]"

    # 3. MS unclear → cap conviction (exception: confirmed channel bounce)
    bounce_ok = ch_rejection is not None and (
        (ch_rejection["type"] == "support_bounce"    and _sig_side(signal.get("signal")) == "BUY") or
        (ch_rejection["type"] == "resistance_reject" and _sig_side(signal.get("signal")) == "SELL")
    )
    if ms.get("pattern") == "none" and signal.get("conviction") in ("HIGH", "MEDIUM"):
        if bounce_ok and signal.get("conviction") == "MEDIUM":
            log.info(f"AI [{symbol}:{res}] MS unclear nhưng CHẠM-BẬT kênh xác nhận → giữ MEDIUM")
        else:
            log.info(f"AI [{symbol}:{res}] MS unclear conviction cap: {signal['conviction']} → LOW")
            signal["conviction"] = "LOW"
            signal["analysis"]   = (signal.get("analysis") or "") + " [MS chưa rõ → conviction=LOW]"

    # 4. Candle reversal guard (shooting star blocks BUY; hammer blocks SELL)
    if reversal:
        rtype = reversal["type"]
        if rtype == "shooting_star" and _sig_side(signal.get("signal")) == "BUY":
            log.info(f"AI [{symbol}:{res}] shooting star → {signal['signal']}→WAIT")
            signal["signal"] = "WAIT"
            signal["conviction"] = "LOW"
            signal["analysis"] = (signal.get("analysis") or "") + " [Shooting star → WAIT]"
            signal["entry_zone"] = signal["target"] = signal["stop_loss"] = None
        elif rtype == "hammer" and _sig_side(signal.get("signal")) == "SELL":
            log.info(f"AI [{symbol}:{res}] hammer → {signal['signal']}→WAIT")
            signal["signal"] = "WAIT"
            signal["conviction"] = "LOW"
            signal["analysis"] = (signal.get("analysis") or "") + " [Hammer → WAIT]"
            signal["entry_zone"] = signal["target"] = signal["stop_loss"] = None

    # 5. SL cooldown — cap conviction to MEDIUM after a recent loss
    if await r.exists(f"ai:sl_cd:{symbol}:{res}") and signal.get("signal") in _ALL_DIRECTIONAL and signal.get("conviction") == "HIGH":
        log.info(f"AI [{symbol}:{res}] SL cooldown active → conviction HIGH→MEDIUM")
        signal["conviction"] = "MEDIUM"
        signal["analysis"]   = (signal.get("analysis") or "") + " [SL gần đây → conviction≤MEDIUM]"

    # 6. TP zone clamp — cap TP just before nearest S/R zone
    atr_v = (momentum or {}).get("atr") or 0
    buf   = 0.10 * atr_v
    if _sig_side(signal.get("signal")) == "BUY" and signal.get("target") and bar_list:
        res_zones = (sr_zones or {}).get("resistances") or []
        if res_zones and atr_v > 0:
            ceiling = res_zones[0]["lo"] - buf
            if signal["target"] > ceiling:
                capped = round(ceiling, 2)
                log.info(f"AI [{symbol}:{res}] {signal['signal']} TP {signal['target']} > vùng cản {res_zones[0]['lo']} → {capped}")
                signal["target"] = capped
        else:
            max_swing = max(b["high"] for b in bar_list[-50:])
            if signal["target"] > max_swing * 1.005:
                signal["target"] = round(max_swing * 1.002, 2)
    elif _sig_side(signal.get("signal")) == "SELL" and signal.get("target") and bar_list:
        sup_zones = (sr_zones or {}).get("supports") or []
        if sup_zones and atr_v > 0:
            floor = sup_zones[0]["hi"] + buf
            if signal["target"] < floor:
                capped = round(floor, 2)
                log.info(f"AI [{symbol}:{res}] {signal['signal']} TP {signal['target']} < vùng hỗ trợ {sup_zones[0]['hi']} → {capped}")
                signal["target"] = capped
        else:
            min_swing = min(b["low"] for b in bar_list[-50:])
            if signal["target"] < min_swing * 0.995:
                signal["target"] = round(min_swing * 0.998, 2)

    # 7. SL sanity — null SL when SL ≈ entry (within 0.01%), then downgrade to WAIT
    raw_entry, raw_sl = signal.get("entry_zone"), signal.get("stop_loss")
    if raw_entry and raw_sl and abs(raw_sl - raw_entry) / raw_entry < 0.0001:
        log.warning(f"AI [{symbol}:{res}] SL≈entry ({raw_sl}≈{raw_entry}) — nulling stop_loss")
        signal["stop_loss"] = None
        if signal.get("signal") in _ALL_DIRECTIONAL:
            signal["signal"] = "WAIT"
            signal["conviction"] = "LOW"
            signal["analysis"] = (signal.get("analysis") or "") + " [SL không hợp lệ → WAIT]"

    # 8. Minimum R:R — reject directional signals with reward:risk < 1.2
    if signal.get("signal") in _ALL_DIRECTIONAL:
        e, sl, tp = signal.get("entry_zone"), signal.get("stop_loss"), signal.get("target")
        if e and sl and tp:
            risk, reward = abs(e - sl), abs(tp - e)
            if risk > 0 and reward / risk < 1.2:
                log.info(f"AI [{symbol}:{res}] R:R {reward/risk:.2f} < 1.2 → WAIT")
                signal["signal"] = "WAIT"
                signal["conviction"] = "LOW"
                signal["analysis"] = (signal.get("analysis") or "") + f" [R:R {reward/risk:.1f}<1.2 → WAIT]"

    # 9. HTF confluence — never enter against a higher timeframe
    if signal.get("signal") in _ALL_DIRECTIONAL and htf_context:
        our_side = _sig_side(signal["signal"])
        opp_side = "SELL" if our_side == "BUY" else "BUY"
        htf_to_check = [h for h in ("15", "60") if int(h) > int(res)]
        if any(_sig_side((htf_context.get(h) or {}).get("signal")) == opp_side for h in htf_to_check):
            log.info(f"AI [{symbol}:{res}] HTF opposes {signal['signal']} → WAIT")
            signal["analysis"]   = (signal.get("analysis") or "") + " [Ngược khung lớn → WAIT]"
            signal["signal"]     = "WAIT"
            signal["conviction"] = "LOW"

    return signal


# ── Fallback TP computation ───────────────────────────────────────────────────────

def _compute_fallback_tps(data: dict, bar_list: list[dict], ms: dict,
                           momentum: dict, res: str) -> None:
    """Fill target1/2/3 using EMA + swing pivots + ATR when AI returned no TPs."""
    _entry, _sl = data.get("entry_zone"), data.get("stop_loss")
    if not (_entry and _sl and not data.get("target1")):
        return

    _is_sell = data.get("signal") == "SELL"
    _dist    = abs(_entry - _sl)
    _tps: list[float] = []

    # 1. EMA anchor — 1H uses EMA50 (institutional), others use EMA25
    _ema = _h1_ema50_value(bar_list) if res == "60" else _ema25_value(bar_list)
    if _ema and _dist > 0:
        if _is_sell and _ema < _entry and _ema > (_entry - 3 * _dist):
            _tps.append(round(_ema, 5))
        elif not _is_sell and _ema > _entry and _ema < (_entry + 3 * _dist):
            _tps.append(round(_ema, 5))

    # 2. Swing pivots from MS waves
    _waves = ms.get("waves") or []
    if _is_sell:
        _swing = sorted([w["price"] for w in _waves if w.get("type") == "low" and w["price"] < _entry], reverse=True)
    else:
        _swing = sorted([w["price"] for w in _waves if w.get("type") == "high" and w["price"] > _entry])
    _tps.extend([round(p, 5) for p in _swing if p not in _tps])

    # 2b. 1H extra: PDH/PDL + EMA50/200
    if res == "60" and len(bar_list) >= 48:
        _yesterday = bar_list[-48:-24]
        if len(_yesterday) >= 10:
            _pdh     = max(b['high'] for b in _yesterday)
            _pdl     = min(b['low']  for b in _yesterday)
            _closes  = [b['close'] for b in bar_list]
            _ema50   = _h1_ema50_value(bar_list)
            _ema200  = _ema_last(_closes, 200) if len(_closes) >= 210 else None
            _candidates = [p for p in [_pdh, _pdl, _ema50, _ema200] if p is not None]
            _h1_tps = (sorted([p for p in _candidates if p < _entry], reverse=True) if _is_sell
                       else sorted(p for p in _candidates if p > _entry))
            for _p in _h1_tps:
                _rp = round(_p, 5)
                if _rp not in _tps:
                    _tps.append(_rp)

    # 3. ATR fallback — guarantees at least 3 levels
    if _dist > 0:
        _sign  = -1 if _is_sell else 1
        _mults = [1.5, 2.5, 3.5] if res == "60" else [1.0, 1.5, 2.0]
        for _mult in _mults:
            _c = round(_entry + _sign * _mult * _dist, 5)
            if _c not in _tps:
                _tps.append(_c)

    if _tps:
        data["target1"] = _tps[0]
        data["target2"] = _tps[1] if len(_tps) > 1 else None
        data["target3"] = _tps[2] if len(_tps) > 2 else None
        data["target"]  = data["target1"]


# ── Core analysis ────────────────────────────────────────────────────────────────

async def _analyze_one(r: aioredis.Redis, symbol: str, res: str, quote: dict | None,
                       force: bool = False, htf_context: dict | None = None,
                       trigger_event: str = "manual") -> dict:
    ms, bar_list = await _compute_ms_for_ai(r, symbol, res)
    momentum     = _detect_momentum(bar_list, res)
    bias         = _trend_bias(bar_list, res)
    reversal     = _detect_candle_reversal(bar_list)
    ch_rejection = _detect_channel_rejection(bar_list, ms.get("channel"))
    sr_zones     = _sr_zones(bar_list, ms)
    rel_volume   = _relative_volume(bar_list)
    vwap         = _vwap(bar_list)
    sr_probe     = _detect_sr_probe(bar_list, sr_zones, (momentum or {}).get("atr") or 0.0)

    if not force:
        if (locked := await _check_active_lock(r, symbol, res, quote)) is not None:
            return locked
        if (stable := await _check_stability_gate(r, symbol, res, ms, quote, momentum)) is not None:
            return stable

    if momentum.get('level') != 'LOW':
        log.info(f"AI [{symbol}:{res}] momentum={momentum['level']} {momentum.get('direction','')} pct={momentum.get('pct_change',0):.2f}%")

    # Load previous analysis and current user-drawn position
    prev_trade: dict | None = None
    try:
        prev_raw = await r.get(f"ai_analysis:{symbol}:{res}")
        if prev_raw:
            prev_trade = json.loads(prev_raw)
    except Exception:
        pass

    user_trade: dict | None = None
    try:
        ut_raw = await r.get(f"mt5:user_trade:{symbol}:{res}")
        if ut_raw:
            user_trade = json.loads(ut_raw)
    except Exception:
        pass
    if user_trade:
        outcome = await _position_outcome(
            symbol, res, user_trade.get("side"), user_trade.get("entry_time_ms"),
            user_trade.get("stop"), user_trade.get("target"))
        if outcome == "open":
            outcome = _position_status(user_trade, (quote or {}).get("bid"))
        if outcome != "open":
            log.info(f"AI [{symbol}:{res}] user position closed ({outcome}) — dropping trade management")
            user_trade = None
            await r.delete(f"mt5:user_trade:{symbol}:{res}")

    # Build prompt context strings
    recent_action_str = _recent_action(bar_list)
    winrate_line = ""
    wr = await _recent_winrate(symbol, res)
    if wr and wr["decided"] >= 4:
        pct  = (wr["rate"] or 0) * 100
        tone = ("ĐÁNG TIN — có thể tự tin hơn" if pct >= 55
                else "KÉM — hãy RẤT thận trọng, ưu tiên WAIT/conviction thấp" if pct < 40
                else "trung bình — giữ kỷ luật")
        winrate_line = (f"Hiệu quả tín hiệu gần đây [{res}]: {wr['wins']} thắng / {wr['losses']} thua "
                        f"({pct:.0f}% thắng) → {tone}.\n")

    if res == "60":
        ltf_context_str = await _ltf_context(r, symbol, "15") + await _ltf_context(r, symbol, "5")
    elif res == "15":
        ltf_context_str = await _ltf_context(r, symbol, "5")
    else:
        ltf_context_str = ""

    regime = _trend_regime(bar_list, ms.get("channel"))
    regime_line = (
        f"TREND REGIME: {regime['regime']} ({regime['label']}, độ mạnh {regime['score']:+d}/100). "
        f"Đây là XU HƯỚNG CHỦ ĐẠO của khung này — ưu tiên giao dịch THUẬN regime; "
        f"tín hiệu NGƯỢC regime chỉ được conviction=LOW.\n"
    )
    ema25_line   = _ema25_context(bar_list)
    session_line = _session_context(bar_list)
    h1_line      = _h1_key_levels(bar_list) if res == "60" else ""

    user_prompt = _build_ai_user_prompt(symbol, res, ms, quote, momentum, htf_context,
                                        prev_trade, user_trade, bias, recent_action_str,
                                        winrate_line, ltf_context_str, regime_line, regime,
                                        reversal=reversal, ch_rejection=ch_rejection,
                                        sr_zones=sr_zones, rel_volume=rel_volume, vwap=vwap,
                                        sr_probe=sr_probe, ema25_line=ema25_line,
                                        session_line=session_line, h1_line=h1_line)
    signal, token_stats = await _call_claude(user_prompt)

    signal = await _apply_signal_guards(r, signal, symbol, res, ms, bias, reversal,
                                        ch_rejection, sr_zones, momentum, htf_context,
                                        prev_trade, bar_list)

    # rule_signal is the ground truth — AI conviction/analysis annotates it
    rule_signal_val = ms.get("rule_signal", {}).get("signal") or "WAIT"

    data: dict = {
        "symbol":      symbol,
        "resolution":  res,
        "signal":      rule_signal_val,
        "conviction":  signal.get("conviction", "LOW"),
        "win_pct":     signal.get("win_pct"),
        "trigger":     signal.get("trigger"),
        "watch_buy":   None,
        "watch_sell":  None,
        "key_level":   None,
        "est_bars":    None,
        "analysis":    signal.get("analysis", ""),
        "entry_zone":  signal.get("entry_zone"),
        "target":      signal.get("target1"),
        "target1":     signal.get("target1"),
        "target2":     signal.get("target2"),
        "target3":     signal.get("target3"),
        "stop_loss":   signal.get("stop_loss"),
    }

    # Fallback entry + SL when AI omitted them for a directional signal
    if rule_signal_val in ("BUY", "SELL") and not data.get("entry_zone"):
        bid, atr_ = (quote or {}).get("bid"), (momentum or {}).get("atr") or 0
        if bid and atr_ > 0:
            sign = -1 if rule_signal_val == "BUY" else 1
            data["entry_zone"] = round(bid, 5)
            data["stop_loss"]  = round(bid + sign * 2.0 * atr_, 5)
            log.info(f"AI [{symbol}:{res}] entry/SL fallback (AI null): entry={data['entry_zone']} sl={data['stop_loss']}")

    _compute_fallback_tps(data, bar_list, ms, momentum, res)

    _ch = ms.get("channel") or {}
    data.update({
        "trade_status":       None,
        "trade_note":         None,
        "prediction_updated": None,
        "update_reason":      None,
        "analysis_bid":       quote.get("bid") if quote else None,
        "token_stats":        token_stats,
        "ms_pattern":         ms.get("pattern",    "none"),
        "ms_confidence":      ms.get("confidence", 0.0),
        "regime":             regime.get("regime"),
        "regime_label":       regime.get("label"),
        "regime_score":       regime.get("score"),
        "timestamp_ms":       int(time.time() * 1000),
        "context": {
            "ms_pattern":    ms.get("pattern"),
            "ms_confidence": ms.get("confidence"),
            "pivots_count":  ms.get("pivots_count"),
            "channel":   {k: _ch.get(k) for k in ("pos", "direction", "slope_pct", "upper", "lower", "mid")},
            "regime":    {"regime": regime.get("regime"), "score": regime.get("score"), "votes": regime.get("votes")},
            "momentum":  {"level": momentum.get("level"), "direction": momentum.get("direction"), "pct_change": momentum.get("pct_change")},
            "bias":      {"bias": bias.get("bias"), "net": bias.get("net")},
            "atr":       (momentum or {}).get("atr"),
        },
    })

    # Block WAIT from overwriting an active BUY/SELL that hasn't hit TP/SL
    if data["signal"] == "WAIT" and prev_trade and prev_trade.get("signal") in _ALL_DIRECTIONAL:
        _prev_sig = prev_trade["signal"]
        _bid_chk  = (quote or {}).get("bid")
        _resolved = False
        if prev_trade.get("stop_loss") and prev_trade.get("target") and _bid_chk:
            _live = _position_status(
                {"side": _sig_side(_prev_sig), "stop": prev_trade["stop_loss"], "target": prev_trade["target"]},
                _bid_chk)
            _resolved = _live in ("tp", "sl")
        if not _resolved:
            log.info(f"AI [{symbol}:{res}] chặn WAIT overwrite {_prev_sig} (chưa chạm TP/SL) → giữ card cũ")
            await r.expire(f"ai_analysis:{symbol}:{res}", _AI_LOCK_TTL)
            return prev_trade

    serialized = json.dumps(data, default=float)
    cache_ttl  = _AI_LOCK_TTL if data["signal"] in _ALL_DIRECTIONAL else config._AI_TTL
    pipe = r.pipeline()
    pipe.set(f"ai_analysis:{symbol}:{res}", serialized, ex=cache_ttl)
    pipe.publish("mt5:ai_analysis", serialized)
    await pipe.execute()
    log.info(
        f"AI [{symbol}:{res}] {data['signal']} ({data['conviction']}) "
        f"[{trigger_event}]  "
        f"↑{token_stats['input_tokens']} ↓{token_stats['output_tokens']} tokens"
    )
    asyncio.create_task(_insert_ai_prediction(data, trigger_event))
    return data


# ── Coalescing + tiered analysis ─────────────────────────────────────────────────

async def analyze_coalesced(r: aioredis.Redis, symbol: str, res: str, quote: dict | None,
                            force: bool = False, htf_context: dict | None = None,
                            trigger_event: str = "manual") -> dict:
    """Coalesce concurrent force=False analyses for (symbol, res) onto ONE Claude call.
    force=True (manual/reset) always runs fresh — bypasses dedup."""
    if force:
        return await _analyze_one(r, symbol, res, quote, True, htf_context, trigger_event)

    key = (symbol, res)
    existing = state.ai_inflight.get(key)
    if existing is not None and not existing.done():
        return await existing

    task = asyncio.ensure_future(
        _analyze_one(r, symbol, res, quote, False, htf_context, trigger_event)
    )
    state.ai_inflight[key] = task
    task.add_done_callback(
        lambda t, k=key: state.ai_inflight.pop(k, None) if state.ai_inflight.get(k) is t else None
    )
    return await task


_HTF_ORDER = ("60", "15")   # highest first — HTF completes before LTF reads it


async def analyze_tiered(r: aioredis.Redis, symbol: str, resolutions: list[str],
                         quote: dict | None, *, force: bool = False,
                         htf_context: dict | None = None,
                         trigger_event: "str | dict" = "manual") -> dict:
    """Top-down tiered analysis: HTF (60→15) sequentially, lower frames in parallel.
    Each HTF result is injected into htf_context so lower frames see the FRESH read."""
    htf_context = dict(htf_context or {})
    req = set(resolutions)
    results: dict[str, dict] = {}

    def _te(res: str) -> str:
        return trigger_event.get(res, "manual") if isinstance(trigger_event, dict) else trigger_event

    for hres in _HTF_ORDER:
        if hres not in req:
            continue
        try:
            data = await analyze_coalesced(r, symbol, hres, quote, force=force,
                                           htf_context=htf_context, trigger_event=_te(hres))
            results[hres] = data
            htf_context[hres] = data
        except Exception as e:
            log.error(f"AI tiered HTF [{symbol}:{hres}] failed: {e}")
            results[hres] = e

    lower = [res for res in resolutions if res not in _HTF_ORDER]
    if lower:
        gathered = await asyncio.gather(
            *[analyze_coalesced(r, symbol, res, quote, force=force,
                                htf_context=htf_context, trigger_event=_te(res)) for res in lower],
            return_exceptions=True,
        )
        results.update(zip(lower, gathered))
    return results


async def _analyze_single(symbol: str, res: str, trigger_event: str = "user_trade") -> dict | None:
    """Run a fresh forced analysis for ONE resolution (used when user draws/updates a position)."""
    if (config.AI_BACKEND != "local" and not state.ai_client) or symbol not in config.SYMBOLS:
        return None
    r = aioredis.Redis(connection_pool=state.redis_pool)
    quote_raw = await r.hgetall(f"mt5:quote:{symbol}")
    quote = {k: float(v) for k, v in quote_raw.items()} if quote_raw else None
    htf_context = await _fetch_htf_context(r, symbol)
    try:
        return await _analyze_one(r, symbol, res, quote, force=True,
                                  htf_context=htf_context, trigger_event=trigger_event)
    except Exception as e:
        log.error(f"user_trade analyze [{symbol}:{res}] failed: {e}")
        return None
