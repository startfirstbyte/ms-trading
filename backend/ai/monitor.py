"""Background loop that auto-triggers AI analysis on candle close."""
import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

from backend.core import config, state
from backend.ai.analyzer import analyze_tiered, _fetch_htf_context
from backend.positions import _position_status, _sig_side, _ALL_DIRECTIONAL, _LIMIT_SIGNALS, _TRIGGER_RISE, _TRIGGER_FALL

log = logging.getLogger(__name__)

_ai_monitor_cooldown: dict[str, float] = {}   # key = "symbol:res" → last trigger time
_MONITOR_COOLDOWN = 180     # seconds — min gap between auto-triggers per symbol:res


async def _ai_monitor_loop() -> None:
    """
    Background loop (30s tick) — triggers AI analysis on candle close.
    Respects server-wide kill-switch (ai:auto_enabled) and per-TF control
    (ai:tf_enabled:{symbol}:{res} == "0" → skip that timeframe).
    Manual Analyze button is never affected.
    """
    await asyncio.sleep(15)   # wait for server warmup
    log.info("AI monitor loop started")

    while True:
        try:
            if config.AI_BACKEND != "local" and not state.ai_client:
                await asyncio.sleep(30)
                continue

            r = aioredis.Redis(connection_pool=state.redis_pool)

            # Server-wide kill-switch
            if await r.get(config.AI_AUTO_FLAG_KEY) == "0":
                await asyncio.sleep(30)
                continue

            now = time.time()

            for symbol in config.SYMBOLS:
                quote_raw = await r.hgetall(f"mt5:quote:{symbol}")
                if not quote_raw:
                    continue
                try:
                    bid = float(quote_raw.get("bid", 0))
                except (ValueError, TypeError):
                    continue
                if not bid:
                    continue

                plan: dict[str, str] = {}

                for res in config._AI_RESOLUTIONS:
                    # Per-TF kill-switch ("0" = disabled; absent = enabled)
                    if await r.get(f"{config.AI_TF_ENABLED_PREFIX}:{symbol}:{res}") == "0":
                        continue

                    if now - _ai_monitor_cooldown.get(f"{symbol}:{res}", 0) < _MONITOR_COOLDOWN:
                        continue

                    closed_key = f"mt5:candle_closed:{symbol}:{res}"
                    if await r.exists(closed_key):
                        await r.delete(closed_key)
                        # Pre-filter: chỉ gọi AI khi rule_signal != WAIT
                        ms_raw = await r.get(f"ms:{symbol}:{res}")
                        rule_sig = "WAIT"
                        if ms_raw:
                            try:
                                ms_data  = json.loads(ms_raw)
                                rule_sig = ms_data.get("rule_signal", {}).get("signal", "WAIT")
                            except Exception:
                                pass
                        if rule_sig == "WAIT":
                            log.debug(f"AI skip [{symbol}:{res}] rule_signal=WAIT")
                            continue
                        plan[res] = f"candle_close:{rule_sig}"
                    else:
                        # TP/SL resolved between candle closes — don't wait for next close
                        locked_raw = await r.get(f"ai_analysis:{symbol}:{res}")
                        if locked_raw:
                            try:
                                c = json.loads(locked_raw)
                                csig = c.get("signal")
                                if csig in _ALL_DIRECTIONAL:
                                    c_entry = c.get("entry_zone")
                                    c_sl, c_tp = c.get("stop_loss"), c.get("target")
                                    if c_sl and c_tp:
                                        live = _position_status(
                                            {"side": _sig_side(csig), "stop": c_sl, "target": c_tp}, bid)
                                        if live in ("tp", "sl"):
                                            plan[res] = f"{live}_resolved"
                                            # Xóa cache ngay — frontend không còn thấy signal cũ (cả TP lẫn SL)
                                            await r.delete(f"ai_analysis:{symbol}:{res}")
                                        elif csig in _LIMIT_SIGNALS and c_entry:
                                            # Stale limit: giá đi sai hướng > 1× SL distance
                                            sl_dist = abs(c_sl - c_entry)
                                            if sl_dist and csig in _TRIGGER_RISE and bid < c_entry - sl_dist:
                                                plan[res] = "stale_limit"
                                            elif sl_dist and csig in _TRIGGER_FALL and bid > c_entry + sl_dist:
                                                plan[res] = "stale_limit"
                            except Exception:
                                pass

                if not plan:
                    continue

                htf_context = await _fetch_htf_context(r, symbol)
                quote_dict  = {k: float(v) for k, v in quote_raw.items() if v}
                for res, reason in plan.items():
                    _ai_monitor_cooldown[f"{symbol}:{res}"] = now
                    log.info(f"AI auto-trigger [{symbol}:{res}] reason={reason}")

                asyncio.create_task(
                    analyze_tiered(r, symbol, list(plan.keys()), quote_dict,
                                   force=False, htf_context=htf_context,
                                   trigger_event=dict(plan))
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning(f"AI monitor loop error: {e}")

        await asyncio.sleep(30)
