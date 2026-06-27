"""Smoke test: gọi bridge bằng ĐÚNG _AI_SYSTEM thật + parse y hệt _call_claude."""
import json, sys, urllib.request

sys.path.insert(0, ".")
from backend.ai.prompt import _AI_SYSTEM

USER = (
    "Symbol: XAUUSD (5m)\n"
    "Current price: bid=2345.50000  ask=2345.80000\n"
    "ATR14≈1.20000 (SL PHẢI ≥ 1.2×ATR = 1.44000 từ entry)\n"
    "TREND REGIME: STRONG_DOWN (giảm mạnh, độ mạnh -78/100).\n"
    "⚠ MOMENTUM HIGH DOWN: giá đã -0.85% trong 10 bars, bar_ratio=2.3x ATR. KHÔNG BUY.\n"
    "Market Structure: BOS_DOWN confidence=80% complete=True\n"
    "Trend: bearish  Event: BOS  Prediction: down\n"
    "BOS level broken: 2347.00000\n"
    "Price channel (down, quality=70%):\n  Resistance: 2350.0\n  Support: 2340.0\n  Mid: 2345.0\n"
    "  Current position in channel: 55% (0%=at support, 100%=at resistance)\n"
)

req = urllib.request.Request(
    "http://127.0.0.1:8088/analyze",
    data=json.dumps({"system": _AI_SYSTEM, "prompt": USER, "model": "sonnet"}).encode(),
    headers={"Content-Type": "application/json"},
)
d = json.loads(urllib.request.urlopen(req, timeout=130).read())
raw = d["result"].strip()

# === parse y hệt _call_claude ===
start, end = raw.find("{"), raw.rfind("}") + 1
sig = json.loads(raw[start:end]) if start >= 0 and end > start else {}

print("RAW:", repr(raw[:200]))
print("tokens: in=%d out=%d" % (d["input_tokens"], d["output_tokens"]))
print("--- parsed signal ---")
print(json.dumps(sig, ensure_ascii=False, indent=2))

# === kiểm tra conform schema ===
ok_sig  = sig.get("signal") in ("BUY", "SELL", "WAIT")
ok_conv = sig.get("conviction") in ("HIGH", "MEDIUM", "LOW")
has_keys = all(k in sig for k in ("signal", "conviction", "analysis", "entry_zone", "target", "stop_loss"))
print("\nCONFORM: signal_enum=%s conviction_enum=%s required_keys=%s" % (ok_sig, ok_conv, has_keys))
print("RESULT:", "PASS ✓" if (ok_sig and ok_conv and has_keys) else "FAIL ✗")
