---
name: python-backend
description: >
  Chuẩn quản lý code Python & FastAPI backend của trading project, và playbook tái
  cấu trúc file lớn. Load khi: tách/refactor file Python lớn (đặc biệt server.py →
  backend/ package), thêm router/endpoint mới, tổ chức module, xử lý circular import,
  viết lifespan/background task, đặt type hint/async/config theo best practice, hay
  bất kỳ câu hỏi về cấu trúc thư mục backend.
---

# Python Backend Skill

## Mục đích
Skill này định nghĩa **cách tổ chức code Python phía server** và **playbook tách file lớn**
cho trading project. Dùng khi:
- Tách `server.py` (2200 dòng) thành package `backend/` (mục tiêu đã chốt — xem bên dưới)
- Thêm endpoint / router / background task mới vào backend
- Refactor bất kỳ module Python nào quá lớn để đọc
- Nghi ngờ circular import, global state, lifespan wiring
- Cần áp chuẩn type hint / async / config / error handling

> Backend hiện tại baked vào Docker image (`Dockerfile.server`) → **mọi thay đổi code
> backend phải rebuild**: `podman compose build server && podman compose up -d server`.

---

## 1. Nguyên tắc tổ chức Python (best practice)

**Một module = một concern.** Đặt tên theo việc nó làm, không theo loại (`postgres.py`,
`indicators.py` — KHÔNG `utils2.py`, `helpers.py` tạp nham). Ngưỡng tách: một file > ~400–500
dòng hoặc trộn ≥3 concern khác nhau là tín hiệu nên tách.

**Tầng rõ ràng, phụ thuộc một chiều:**
```
routers/  (HTTP/WS — mỏng, chỉ điều phối)  →  services (ai/, db/, positions)  →  core/ (config, state)
```
Router KHÔNG chứa business logic; nó gọi service. Service KHÔNG biết gì về `Request`/`WebSocket`.
Phụ thuộc luôn trỏ **xuống** (router→service→core), không bao giờ ngược lên.

**Type hint mọi public function.** `def f(symbol: str, res: str) -> dict:`. Dùng `X | None`
(PEP 604) như code hiện tại. Pydantic model cho mọi payload vào/ra.

**Async-first, không block event loop.** I/O (Redis, PG, HTTP, Anthropic) phải `await`.
Việc CPU nặng (detect MS trên 300 bar) nếu thành nút cổ chai → `asyncio.to_thread`.

**Config tập trung, đọc env một chỗ.** Mọi `os.environ.get(...)` và hằng số sống trong
`core/config.py`. Không rải `os.environ` khắp nơi.

**Lỗi rõ ràng.** API → `HTTPException(status, detail)`. Background task → `try/except` + `log`
quanh vòng lặp để 1 lỗi không giết cả task (xem `_pg_cleanup_loop`, `_pubsub_listener`).

---

## 2. CIRCULAR IMPORT — luật quan trọng nhất khi tách

`server.py` dùng global mutable: `redis_pool`, `pg_pool`, `_ai_client`, `manager`. Khi tách ra
nhiều file, **đừng** `from core.state import redis_pool` — lệnh đó copy giá trị `None` tại thời
điểm import (trước khi `lifespan` gán pool) và không bao giờ cập nhật.

**Pattern đúng — import MODULE, truy cập thuộc tính lúc chạy:**
```python
# core/state.py  — ô chứa state, được lifespan ghi vào lúc startup
redis_pool = None          # aioredis.ConnectionPool | None
pg_pool    = None          # asyncpg.Pool | None
ai_client  = None          # anthropic.AsyncAnthropic | None
manager    = ConnectionManager()   # singleton an toàn để tạo sớm

# bất kỳ module nào cần pool:
from backend.core import state
async def foo():
    r = aioredis.Redis(connection_pool=state.redis_pool)   # đọc lúc gọi → luôn mới
```
```python
# backend/main.py
from backend.core import state
@asynccontextmanager
async def lifespan(app):
    state.redis_pool = aioredis.ConnectionPool.from_url(config.REDIS_URL, decode_responses=True, ...)
    state.pg_pool    = await asyncpg.create_pool(config.DATABASE_URL, ...)
    state.ai_client  = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    tasks = [asyncio.create_task(t()) for t in (pubsub_listener, pg_cleanup_loop, ai_monitor_loop)]
    yield
    for t in tasks: t.cancel()
```
Quy tắc: **`state.x` (qua module), không `from state import x`.** Đây là điểm dễ vỡ nhất.

**App factory + APIRouter** để tránh router import ngược `app`:
```python
# routers/history.py
from fastapi import APIRouter
router = APIRouter(prefix="/api", tags=["history"])
@router.get("/history")
async def get_history(...): ...

# backend/main.py
app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
for r in (history.router, websocket.router, webhooks.router, market_structure.router, ai.router, positions.router):
    app.include_router(r)
```

---

## 3. Cấu trúc `backend/` HIỆN TẠI (full FastAPI package — đã tách xong từ server.py)

```
backend/
  __init__.py
  main.py                app factory + lifespan + include_router + CORS  (← từ L316-370)
  models.py              Pydantic: BarData/QuoteData/TickPayload/BatchBarsPayload  (L156-180)
  positions.py           service: _position_status/_position_outcome/_reconcile_user_positions (L2065-2146)
  core/
    __init__.py
    config.py            env + hằng số: REDIS_URL, DATABASE_URL, SYMBOLS, _AI_RESOLUTIONS,
                         TIMEFRAME_MAP, TTL, ngưỡng monitor/drift  (phần const của L51-155)
    state.py             redis_pool / pg_pool / ai_client / manager (ô global mutable)
    ws_manager.py        class ConnectionManager  (L181-216)
    auth.py              verify_webhook  (L217-224)
  db/
    __init__.py
    postgres.py          _init_pg_schema/_insert_ai_prediction/_upsert_bars_pg/
                         _query_bars_pg/_query_bars_pg_before/_pg_cleanup_loop  (L371-596)
    realtime.py          _pubsub_listener + manager.broadcast wiring  (L597-634)
  ai/
    __init__.py
    prompt.py            _AI_SYSTEM (L75-148) + _build_ai_user_prompt (L1204-1450)
    indicators.py        momentum/trend_bias/recent_action/ema/atr/regime/
                         read_recent_bars/ltf_context  (L1451-1674)
    winrate.py           _recent_winrate + winrate helpers  (L1675-1711)
    analyzer.py          _compute_ms_for_ai/_call_claude/_analyze_one/_analyze_single  (L1712-2064)
    monitor.py           _ai_monitor_loop  (L225-315)
  routers/
    __init__.py
    websocket.py         /ws/{sym}/{res}, /ws/ai/{sym}  (L635-670)
    webhooks.py          /webhook/tick, /webhook/bars/batch  (L671-746)
    history.py           /api/config|symbols|resolve|history|quote + _mt5_history  (L747-922)
    market_structure.py  /api/ms, /api/ms/compute, snapshots  (L923-1119)
    ai.py                /api/ai_analysis|ai_history|winrate|ai/analyze + chart_state  (L1120-1201,1999-2042)
    positions.py         /api/user_position sync_user_positions  (L2147-2200)
  analysis/              MS detector (moved in from former top-level analysis/)
    market_structure.py  detect() = ms_detect; elliott.py = dead code
```

`analysis/` đã **move vào `backend/analysis/`** — import `from backend.analysis.market_structure import detect as ms_detect`. Chỉ `backend/ai/analyzer.py` + `backend/routers/market_structure.py` dùng.

---

## 4. Playbook tách file lớn an toàn (áp cho server.py)

Thứ tự **từ lá lên gốc** (ít phụ thuộc trước), mỗi bước rebuild + smoke test:

1. **Tạo `core/` trước** — `config.py` (hằng số thuần, không phụ thuộc gì) → `state.py` →
   `ws_manager.py` → `auth.py`. Đây là nền, không import ngược.
2. **`models.py`** — Pydantic thuần, chỉ phụ thuộc pydantic.
3. **`db/postgres.py`, `db/realtime.py`** — chỉ phụ thuộc `core.state` + `core.config`.
4. **`ai/`** theo thứ tự `prompt → indicators → winrate → analyzer → monitor` (analyzer phụ
   thuộc 3 cái trước + db + state).
5. **`positions.py`** (service).
6. **`routers/*`** — mỏng, import service. Đây là tầng cuối.
7. **`main.py`** — app factory, lifespan (gán `state.*`, start background task), `include_router`.
8. **Sửa `Dockerfile.server`:**
   ```dockerfile
   COPY backend/ ./backend/
   CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
   ```
   (bỏ `COPY server.py ./`, `COPY analysis/` và `uvicorn server:app` — analysis giờ nằm trong backend/).
9. **Xóa `server.py`** chỉ sau khi backend mới chạy xanh.

**Bảo toàn hành vi:** đây là refactor thuần — KHÔNG đổi logic, KHÔNG đổi path endpoint,
KHÔNG đổi schema Redis/PG. Mọi route giữ nguyên đường dẫn để frontend/worker không phải sửa.

**Verify sau rebuild:**
```bash
podman compose build server && podman compose up -d server
podman logs --tail 30 mt5_server                 # lifespan ok, không traceback import
curl -s localhost:8000/api/quote?symbol=BTCUSD   # HTTP sống
curl -s "localhost:8000/api/history?symbol=BTCUSD&resolution=5&from=$(date -d '-2 hour' +%s)&to=$(date +%s)" | head -c 200
podman exec mt5_redis redis-cli PUBSUB NUMSUB mt5:tick:BTCUSD   # realtime listener ≥1 sau khi có WS client
```
Checklist: app khởi động không lỗi import; cả 4 background task start (pubsub, pg_cleanup,
ai_monitor); endpoint HTTP + WS + webhook trả đúng như trước; `tsc`/frontend không đổi.

---

## 5. Gotchas của project này

- **Baked image** — sửa backend xong KHÔNG có hiệu lực tới khi rebuild server container.
- **`asyncpg` guard** — `requirements.server.txt` phải có `asyncpg>=0.29.0`; nếu thiếu,
  `pg_pool` = None và mọi ghi PG âm thầm bị bỏ qua. Giữ nguyên try/except quanh import.
- **Global pools chỉ tồn tại sau lifespan** — đừng tạo `aioredis.Redis(...)` ở module scope;
  luôn `state.redis_pool` trong thân hàm async.
- **`_AI_SYSTEM` rất dài & nhạy** — khi chuyển sang `ai/prompt.py` phải copy NGUYÊN VĂN (đổi
  một ký tự = đổi hành vi model). Xem `technical-analysis` skill để hiểu prompt.
- **Đổi/so model AI** thuộc địa hạt `technical-analysis` skill (biến `AI_MODEL`).
- **Background task phải được cancel trong lifespan teardown** để rebuild/reload không leak task.

---

## 6. Long-form docs (Obsidian)

Chi tiết "why" đằng sau các quyết định backend — quá dài cho skill này — sống trong Obsidian
vault, `Trading Project/`: [[ADR-001-claude-cli-bridge]] (bridge networking Podman↔host),
[[ADR-002-weekend-history-fallback]] (`/api/history` fallback), [[ADR-003-server-refactor]]
(lịch sử tách server.py — bối cảnh đầy đủ cho §3/§4 ở trên), [[api-endpoints]],
[[redis-pg-schema]], [[worker-data-flow]] (chẩn đoán data đứng phía worker/host).
Query theo `related-skill: python-backend` qua `mcp__obsidian__search_query` nếu cần liệt kê hết.
