# Technical Analysis Skill

## Mục đích
Skill này mô tả toàn bộ pipeline phân tích kỹ thuật của hệ thống — từ raw bars → market structure → AI prediction. Dùng khi:
- Cần sửa/nâng cấp thuật toán detect
- Thêm indicator mới vào prompt AI
- Debug tại sao AI ra signal sai
- Cải thiện SL/TP logic

---

## Stack phân tích

```
MT5 bars (OHLCV)
    ↓
analysis/market_structure.py  ←  detect() — pure Python, no numpy
    ↓ dict (pattern, channel, waves, structure, confidence)
server.py: _compute_elliott_for_ai()
    + _detect_momentum()
    + htf_context (15m, 1H cached results)
    + prev_trade (cached prediction trước đó)
    ↓
_build_ai_user_prompt()  ← assembles text prompt
    ↓
Claude (claude-sonnet-4-6)
    ↓ JSON
AICard {signal, conviction, trigger, entry_zone, target, stop_loss, key_level, ...}
```

---

## market_structure.py — Thuật toán chi tiết

### 1. ZigZag (`_zigzag`)
Tìm pivot high/low bằng ratcheting: mỗi TF có `_TF_DEV` deviation threshold (% price move để confirm pivot).

| TF | Dev (%) | Ý nghĩa |
|----|---------|---------|
| 1m | 0.08    | Rất nhạy — nhiều pivot |
| 3m | 0.18    | |
| 5m | 0.22    | |
| 15m| 0.28    | |
| 1H | 0.70    | Ít pivot, chỉ major swing |

**Gotcha**: Nếu dev quá thấp → noise, AI nhầm. Nếu quá cao → miss entries.

### 2. Swing Classification (`_classify_swings`)
- `HH` (Higher High), `HL` (Higher Low) → bullish structure
- `LH` (Lower High), `LL` (Lower Low) → bearish structure
- So sánh mỗi pivot với cùng type trước đó (high vs high, low vs low)

### 3. Trend Detection (`_detect_trend`)
Nhìn 6 swing gần nhất:
- `bull > bear && bull >= 2` → bullish
- `bear > bull && bear >= 2` → bearish
- Ngược lại → ranging

### 4. BOS / CHOCH (`_find_bos_choch`)
- **BOS (Break of Structure)**: close vượt swing high/low cuối **cùng chiều trend** → tiếp diễn
- **CHOCH (Change of Character)**: close vượt swing high/low cuối **ngược chiều trend** → đảo chiều sớm
- `break_strength`: % khoảng cách price đã đi qua level (normalize bởi recent range)

### 5. Price Channel (`_build_channel`)
Linear regression riêng cho swing highs và swing lows (6 swings gần nhất):
- `quality` = R² trung bình của 2 đường → cao = kênh rõ, thấp = noise
- `pos` = 0.0 ở support, 1.0 ở resistance
- `direction` = up/down/flat dựa trên slope

### 6. Pattern fallback (khi không có BOS/CHOCH)
- channel_support: up channel + pos < 0.25 → bullish
- channel_resistance: down channel + pos > 0.75 → bearish
- range_low: flat + pos < 0.20 → bullish
- range_high: flat + pos > 0.80 → bearish
- mid_channel: không rõ → return `pattern="none"` → AI output WAIT

### 7. Confidence score
```
confidence = (
    0.35 × struct_score    # HH/HL hoặc LH/LL count
  + 0.30 × break_score     # break strength (0.3 cho channel signals)
  + 0.20 × ch_score        # channel quality (R²), min 0.30
  + 0.15 × recency_f       # last pivot gần right edge không?
) × choch_penalty (0.75 nếu CHOCH)
```
Ngưỡng tối thiểu: `< 0.08` → return `pattern="none"`.

---

## AI Prompt pipeline (`server.py`)

### `_detect_momentum(bars, res)`
Tính ATR14 và xem price move 10 bars gần nhất:
- `pct_change` = % price change 10 bars
- `bar_ratio` = pct_change / ATR14 → bao nhiêu lần ATR
- `level` = HIGH (>2.5×ATR) / MEDIUM (>1.5×ATR) / LOW
- `direction` = up/down

### `htf_context`
Với 1m/3m/5m: pre-fetch cached analysis của 15m + 1H trước khi chạy Claude.
Với 15m: chỉ fetch 1H.
Với 1H: không có HTF context.

### `prev_trade` / `PREV_ANALYSIS`
Luôn load cached analysis trước đó (mọi signal kể cả WAIT).
Gửi vào prompt để AI so sánh:
- `prediction_updated`: true nếu cấu trúc đổi
- `update_reason`: lý do ngắn

Nếu prev là BUY/SELL, thêm `PREV_TRADE` block để AI đánh giá HOLD/CLOSE/PARTIAL_TP.

### `_analyze_one(r, symbol, res, quote, force, htf_context, trigger_event)`
Stability gate (khi `force=False`): skip Claude nếu:
1. Same EW pattern
2. Price trong 0.5% của analysis_bid
3. Analysis < 5 phút tuổi

---

## Auto-trigger cơ chế (`_ai_monitor_loop`)
Runs mỗi 30s, cooldown 180s per symbol:res. Mỗi `(symbol, res)` ĐỘC LẬP — key_level là của
RIÊNG từng frame (đọc `ai_analysis:{sym}:{res}.key_level`).

| Event | Điều kiện | Phạm vi |
|-------|-----------|---------|
| candle_close | worker set `mt5:candle_closed:{sym}:{res}` (TTL 120s) | chỉ frame đó |
| key_level_cross | `|bid - key_level| / key_level < 0.2%` | frame đó + **lan xuống khung nhỏ hơn** |
| price_drift | `|bid - analysis_bid| / analysis_bid > 0.5%` | chỉ frame đó |
| htf_key_cross | (lan tỏa) key_level của khung LỚN bị chạm → re-analyze các khung NHỎ hơn | khung nhỏ hơn origin |

**2-pass plan (2026-06-21):** Pass 1 mỗi frame tự xác định trigger (tôn trọng cooldown); Pass 2
nếu frame nào `key_level_cross` thì thêm TẤT CẢ khung nhỏ hơn vào plan với reason `htf_key_cross`
(tôn trọng cooldown từng khung). Mức HTF ảnh hưởng mọi khung nên chạm nó re-analyze cả khung nhỏ;
candle_close/price_drift KHÔNG lan. Active-signal lock chặn tốn kém: frame đang khóa chỉ freeze.

**htf_context cho monitor:** auto-trigger giờ truyền `_fetch_htf_context(r, symbol)` (15m+1H cached)
vào `_analyze_one` → phân tích đơn-frame tự động VẪN có bộ lọc khung lớn (HTF_CONTEXT + guard
"ngược khung lớn"), nhất quán với nút ANALYZE / reset / user_trade. (Trước đó monitor không truyền
htf_context → auto-analysis bỏ qua HTF filter.)

> ⚠ Monitor CHỈ canh `key_level` để bắt cross. `watch_buy`/`watch_sell` và HTF_BUY_ZONE/SELL_ZONE
> KHÔNG phải trigger — chúng chỉ là gợi ý hợp lưu trong prompt. Logic so giá dùng **bid**, không phải ask.

### Concurrency control (2026-06-21) — semaphore + coalescing
Monitor và `/api/ai/analyze` fan-out bằng `asyncio.create_task`/`gather` KHÔNG giới hạn → cao điểm
có thể bắn nhiều call Claude song song (ở `local` mỗi call spawn `claude.exe` ~431MB). Hai lớp chặn:
- **Semaphore (Option 1):** `state.ai_semaphore = Semaphore(config.AI_MAX_CONCURRENCY)` (init trong
  lifespan), bọc đúng phần gọi mạng/CLI trong `_call_claude`. Default **3 (local) / 6 (api)**, override
  env `AI_MAX_CONCURRENCY`. Lock chạy TRƯỚC `_call_claude` nên frame đang khóa KHÔNG tốn slot.
- **Coalescing/dedup (Option 2):** `analyze_coalesced()` (analyzer.py) gom các trigger `force=False`
  trùng `(symbol,res)` lên MỘT task qua `state.ai_inflight` → không gọi Claude lặp. `force=True`
  (manual/reset/user_trade) luôn chạy mới, bypass dedup. Mọi caller force=False (monitor, endpoint)
  đi qua `analyze_coalesced`; `_analyze_single` (force=True) gọi thẳng `_analyze_one`.
- Cả hai là in-memory → reset khi server restart (chỉ điều phối realtime, không phải state bền).
- Đổi trần: sửa `.env` `AI_MAX_CONCURRENCY` → `podman compose up -d server` (recreate đủ, không cần build).

---

## Active-signal lock (khóa tín hiệu đến khi SL/TP)

Khi cache `ai_analysis:{sym}:{res}` đang là **BUY/SELL có đủ entry+TP+SL**, mỗi lần auto
re-analysis (`force=False`, từ `_ai_monitor_loop`) sẽ **đóng băng** tín hiệu thay vì gọi lại Claude:
- `_analyze_one` (đầu hàm) check `_position_outcome` (giá đã chạm TP/SL trên 1m bars kể từ
  `timestamp_ms`?) + `_position_status` (giá live). Còn **"open"** → `return` signal cũ y nguyên,
  KHÔNG gọi Claude, KHÔNG cập nhật.
- Chạm TP/SL → log `"đã chạm TP/SL → phân tích lại"` → tự mở khóa, phân tích mới.
- **TTL:** signal BUY/SELL ghi với `_AI_LOCK_TTL` (30d) thay vì `_AI_TTL` (1h); nhánh lock còn
  `r.expire(...)` gia hạn mỗi lần check → key không hết hạn khi lệnh chưa đóng. WAIT vẫn 1h.
- **Bypass khóa:** nút ANALYZE (`POST /api/ai/analyze?force=true`) và `POST /api/ai/reset?symbol=&resolution=`
  (cả hai dùng `force=True`).
- ⚠ Lock state sống ở Redis (no persistence) → **server/redis restart = mất khóa** (phân tích lại).
  Muốn bền qua restart thì phải lưu trạng thái lock vào PG (chưa làm).

Code: `backend/ai/analyzer.py` (`_analyze_one`, hằng `_AI_LOCK_TTL`). Bảng `ai_predictions` KHÔNG
có cột lock — "tín hiệu hiện tại" là Redis key, lock là logic.

## Schema output AI (JSON)

```json
{
  "signal":             "BUY"|"SELL"|"WAIT",
  "conviction":         "HIGH"|"MEDIUM"|"LOW",
  "trigger":            "≤90 ký tự — giá cụ thể",
  "watch_buy":          number|null,
  "watch_sell":         number|null,
  "key_level":          number|null,
  "est_bars":           integer 1-30|null,
  "analysis":           "≤80 ký tự lý do kỹ thuật",
  "entry_zone":         number|null,
  "target":             number|null,
  "stop_loss":          number|null,
  "trade_status":       "HOLD"|"CLOSE"|"PARTIAL_TP"|null,
  "trade_note":         "≤80 ký tự"|null,
  "prediction_updated": true|false|null,
  "update_reason":      "≤60 ký tự"|null
}
```

### SL Validation (server-side post-process)
Sau khi Claude trả về: nếu `|stop_loss - entry_zone| / entry_zone < 0.01%` → null SL, downgrade → WAIT.

---

## PostgreSQL `ai_predictions` table
Mỗi analysis đều được INSERT với `trigger_event` (manual/candle_close/key_level_cross/price_drift/htf_key_cross/reset/user_trade).
API: `GET /api/ai_history?symbol=&resolution=&limit=20`

---

## Điểm yếu hiện tại & cần cải thiện

### 1. ZigZag không adaptive
Dev threshold cố định per TF. Khi volatility cao (vd: news), cùng dev threshold cho quá nhiều noise. Cần ATR-adaptive dev: `dev = k × ATR14 / price`.

### 2. Channel quality không weighted
`quality = (h_r2 + l_r2) / 2` — hai đường được weight bằng nhau. Thực tế đường support quan trọng hơn trong uptrend và ngược lại.

### 3. HTF context chỉ signal + conviction
Hiện tại `HTF_CONTEXT: 15m=BUY(HIGH), 1H=WAIT(LOW)`. Thiếu key_level của HTF — AI không biết HTF key level ở đâu để canh vùng hợp lưu (confluence).

### 4. Momentum chỉ nhìn 10 bars
`_detect_momentum` dùng 10 bars cố định. 10 bars trên 1H = 10 tiếng — quá dài. 10 bars trên 1m = 10 phút — có thể ổn. Nên `n_bars = max(10, 30 / Number(res))`.

### 5. Stability gate có thể bỏ qua signal quan trọng
5 phút TTL + same pattern = skip. Nhưng trong 5 phút trên 1m = 5 nến, cấu trúc có thể đã đổi hoàn toàn. Cân nhắc giảm xuống 2 phút cho 1m/3m.

### 6. Entry zone = bid (BUY) hoặc ask (SELL)
AI được hướng dẫn dùng ask cho BUY, bid cho SELL. Nhưng không có spread info → AI đôi khi dùng bid cho cả BUY. Cần pass spread vào prompt.

### 7. ~~Không có volume analysis~~ → ĐÃ THÊM (2026-06-21)
`indicators._relative_volume` (volume nến vừa đóng / TB20, ngưỡng ≥1.8 CAO · ≤0.6 THẤP) +
`indicators._vwap` (VWAP neo phiên 00:00 UTC + band ±1σ, fallback rolling 50 nến). Cả hai compute
trong `analyzer._analyze_one`, render thành `volume_line`/`vwap_line` trong `_build_ai_user_prompt`,
luật dùng ở `_AI_SYSTEM` mục "VOLUME & VWAP". Volume = MT5 **tick_volume** (số tick/nến, proxy hoạt
động — không phải khối lượng thật). Còn thiếu: RSI/divergence, spread vào prompt (xem #6).

### 8. ~~Không phát hiện test S/R zone~~ → ĐÃ THÊM (2026-06-21)
`_detect_channel_rejection` chỉ canh biên KÊNH (cần quality≥0.5, bỏ qua nến live) → bỏ sót cú wick
thọc vào **S/R zone** rồi bật, khiến AI ghi nhầm "chưa test cản". Thêm `indicators._detect_sr_probe`
(bars, sr_zones, atr, tol_mult=0.10): quét 3 nến gần nhất **KỂ CẢ nến live**, nếu high/low lọt vào
vùng S/R gần nhất ± `0.10×ATR` tolerance → trả `{side, zone_lo/hi, wick, reacted, bars_ago}`.
`reacted=True` = đã đóng cửa quay khỏi vùng (rejection ở cản / bounce ở hỗ trợ). Render thành
`sr_probe_line` trong prompt; luật ở `_AI_SYSTEM` mục "S/R PROBE" cấm dùng chữ "chưa test" khi probe
tồn tại. Tolerance ATR để cú wick hụt vài điểm vẫn tính là đã test (vd 3m BTCUSD ATR≈47 → tol≈4.7).

---

## Khi debug prediction sai

1. Kiểm tra raw market structure: `GET /api/elliott?symbol=XAUUSD&resolution=1`
   - `pattern="none"` → zigzag không tìm được pivot → tăng bar count hoặc giảm dev
   - `confidence < 0.2` → yếu, AI dễ WAIT
   - `channel.quality < 0.3` → kênh noise

2. Kiểm tra AI prompt cuối: thêm log `log.debug(user_prompt)` trong `_analyze_one`

3. Xem history: `GET /api/ai_history?symbol=XAUUSD&resolution=60&limit=10`
   - So sánh `trigger_event` với market outcome

4. Redis cache: `podman exec mt5_redis redis-cli GET ai_analysis:XAUUSD:60`
