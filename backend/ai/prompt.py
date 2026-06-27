"""AI system prompt + user-prompt builder — simplified win-rate evaluator."""
from datetime import datetime, timezone

from backend.core import config


def _fmt(p) -> str:
    if p is None:
        return "—"
    ap = abs(p)
    if ap > 10000: return f"{p:.0f}"
    if ap > 100:   return f"{p:.2f}"
    if ap > 1:     return f"{p:.3f}"
    return f"{p:.5f}"


# ── Schema ─────────────────────────────────────────────────────────────────────

_AI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "win_pct":    {"type": "integer"},
        "conviction": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "analysis":   {"type": ["string", "null"]},
        "entry_zone": {"type": ["number", "null"]},
        "target1":    {"type": ["number", "null"]},
        "target2":    {"type": ["number", "null"]},
        "target3":    {"type": ["number", "null"]},
        "stop_loss":  {"type": ["number", "null"]},
    },
    "required": ["win_pct", "conviction", "analysis",
                 "entry_zone", "target1", "target2", "target3", "stop_loss"],
}

# ── System prompt ──────────────────────────────────────────────────────────────

_AI_SYSTEM = (
    "You are a trading win-rate evaluator. "
    "A rule-based system has detected a BUY or SELL signal — this signal is FINAL, do NOT change it. "
    "Your job: estimate win probability and set entry/SL/TP for that signal. "
    "Respond with JSON only — no markdown, no explanation.\n"
    'Schema: {"win_pct":0-100,"conviction":"HIGH"|"MEDIUM"|"LOW",'
    '"analysis":"≤60 ký tự tiếng Việt","entry_zone":number|null,'
    '"target1":number|null,"target2":number|null,"target3":number|null,"stop_loss":number|null}\n\n'

    "RULES:\n"
    "· win_pct = xác suất chạm TP1 trước SL (0-100). Ghi thực tế, không làm đẹp.\n"
    "· HTF ngược chiều → hạ win_pct (nhưng KHÔNG đổi signal).\n"
    "· EMA25 dốc ngược signal → hạ win_pct thêm 10-15 điểm.\n"
    "· Session ASIAN → hạ win_pct 10 điểm.\n"
    "· conviction=HIGH chỉ khi win_pct ≥ 65 VÀ HTF cùng chiều VÀ EMA25 thuận.\n"
    "· conviction=LOW khi win_pct < 50.\n"
    "· SL: dưới/trên swing gần nhất, tối thiểu 1.2×ATR từ entry.\n"
    "· target1: swing đối diện gần nhất (R:R ≥ 1.0).\n"
    "· target2: swing tiếp theo hoặc target1 + 1×ATR (R:R ≥ 1.5).\n"
    "· target3: mục tiêu xa nhất hợp lý hoặc target1 + 2×ATR (R:R ≥ 2.0).\n"
    "· Nếu R:R < 1.0: tất cả target=null, entry_zone=null, stop_loss=null.\n"
    "· 1H đặc biệt: TP1 phải neo vào EMA50/PDH/PDL gần nhất (xem H1-LEVELS). "
    "R:R ≥ 1.5 cho TP1, ≥ 2.5 cho TP2, ≥ 3.5 cho TP3. Không đặt TP vượt qua EMA200.\n"
    "· analysis: 1 câu ngắn lý do chính (HTF, EMA, session)."
)


# ── User prompt builder ────────────────────────────────────────────────────────

def _build_ai_user_prompt(symbol: str, res: str, ms: dict, quote: dict | None,
                          momentum: dict | None = None,
                          htf_context: dict | None = None,
                          prev_trade: dict | None = None,
                          user_trade: dict | None = None,
                          bias: dict | None = None,
                          recent_action: str = "",
                          winrate_line: str = "",
                          ltf_context: str = "",
                          regime_line: str = "",
                          regime: dict | None = None,
                          reversal: dict | None = None,
                          ch_rejection: dict | None = None,
                          sr_zones: dict | None = None,
                          rel_volume: dict | None = None,
                          vwap: dict | None = None,
                          sr_probe: dict | None = None,
                          ema25_line: str = "",
                          session_line: str = "",
                          h1_line: str = "") -> str:
    tf   = config._AI_TF_NAMES.get(res, res)
    bid  = quote.get("bid") if quote else None
    ask  = quote.get("ask") if quote else None

    price_line = (
        f"bid={_fmt(bid)}  ask={_fmt(ask)}"
        if bid is not None else "price=unknown"
    )

    atr_val  = (momentum or {}).get("atr")
    atr_line = f"ATR14={_fmt(atr_val)}  MinSL={_fmt(atr_val * 1.2 if atr_val else None)}\n" if atr_val else ""

    # Rule signal
    rule_sig  = ms.get("rule_signal", {})
    sig_val   = rule_sig.get("signal", "WAIT")
    sig_pos   = rule_sig.get("pos", 0.5)
    sig_lbls  = " ".join(rule_sig.get("labels", []))
    rule_line = f"RULE SIGNAL: {sig_val}  channel_pos={sig_pos*100:.0f}%  MS_labels=[{sig_lbls}]\n"

    # HTF context: scalping (1/3/5m) → chỉ 15m; 15m → chỉ 1H; 1H → không có
    htf_line = ""
    if htf_context:
        htf_map = {"1": [("15", "15m")], "3": [("15", "15m")], "5": [("15", "15m")], "15": [("60", "1H")]}
        parts = []
        for htf_res, htf_name in htf_map.get(res, []):
            htf = htf_context.get(htf_res)
            if htf:
                parts.append(f"{htf_name}={htf.get('signal','?')}({htf.get('conviction','?')})")
        if parts:
            htf_line = f"HTF: {', '.join(parts)}\n"

    # MS structure (condensed)
    structure = ms.get("structure") or {}
    waves     = ms.get("waves", [])
    pattern   = ms.get("pattern", "none")
    channel   = ms.get("channel") or {}

    wave_str = "  ".join(
        f"{w['label']}={_fmt(w['price'])}"
        for w in waves[-6:]
    )

    ch_pos  = channel.get("pos")
    ch_dir  = channel.get("direction", "flat")
    ch_line = f"Channel: {ch_dir}  pos={ch_pos*100:.0f}%  upper={_fmt(channel.get('upper'))}  lower={_fmt(channel.get('lower'))}\n" if ch_pos is not None else ""

    # Nearest swing levels
    prices  = [w["price"] for w in waves]
    sw_line = ""
    if bid and prices:
        above = [p for p in prices if p > bid]
        below = [p for p in prices if p < bid]
        parts = []
        if below: parts.append(f"support={_fmt(max(below))}")
        if above: parts.append(f"resistance={_fmt(min(above))}")
        if parts: sw_line = "Swing: " + "  ".join(parts) + "\n"

    return (
        f"Symbol: {symbol} ({tf})  {price_line}\n"
        f"{atr_line}"
        f"{session_line}"
        f"{rule_line}"
        f"{htf_line}"
        f"{ema25_line}"
        f"{h1_line}"
        f"MS: {pattern}  trend={structure.get('trend','?')}  event={structure.get('event','?')}  conf={ms.get('confidence',0):.0%}\n"
        f"Waves: {wave_str}\n"
        f"{ch_line}"
        f"{sw_line}"
        f"{winrate_line}"
    )
