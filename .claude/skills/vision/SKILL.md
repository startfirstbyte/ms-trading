---
name: vision
description: >
  Master orchestrator skill for the MT5 trading chart project. Load this skill
  whenever a task touches this project — adding features, debugging, designing UI,
  reviewing code, running the app, or investigating chart/data issues. Vision
  understands the full stack (MT5 → worker → FastAPI → Redis + PostgreSQL → WebSocket →
  TradingView React) and routes each task to the right skill or agent, pre-loaded with
  the correct context. When the user says "vision" or asks anything about the
  trading project architecture, skills, or workflow — this skill should always
  be active.
---

# Vision — Project Orchestrator

**One-line architecture:**
MT5 → worker.py (Windows host) → HTTP webhook → FastAPI backend (container :8000) → Redis + PostgreSQL → WebSocket → datafeed.ts → TradingView React (container :5173)

---

## STEP 1 — Route the task (read this first)

| Task involves... | Layer | Sub-skill to load | Deploy after change |
|---|---|---|---|
| Bars not flowing, new symbol/TF, MT5 polling | Worker (host) | — | Restart `python worker.py` |
| New endpoint, Redis/PG schema, history logic | Backend (container) | `python-backend` | `podman compose build server && podman compose up -d server` |
| React component, hook, datafeed, TV widget | Frontend (container) | `react-typescript` or `tradingview-setup` | HMR — just save |
| ZigZag/MS/BOS/CHOCH algo, AI prompt, SL/TP | Analysis + AI | `technical-analysis` | Rebuild server |
| Claude bridge, AI_BACKEND, bridge networking | AI infra | — | `./start.ps1` + rebuild server |
| New data field, new full feature (cross-layer) | Cross-stack | Load relevant sub-skills | Worker restart + server rebuild |

**After routing: load the sub-skill — it has the detailed file map, patterns, and gotchas for that layer.**

---

## STEP 2 — Architecture mental model

```
MT5 Terminal (Windows host)
  │  MetaTrader5 (copy_rates_from_pos, symbol_info_tick)
  ▼
worker.py  [HOST — NOT in container]
  │  POST /webhook/tick/{symbol}   every 1s → live bar + closed bar + quote
  │  POST /webhook/bars/batch      on startup → backfill history
  ▼
FastAPI backend  [container mt5_server :8000 — baked image]
  ├─ Redis writes: mt5:live:{sym}:{res} (TTL 30s) · mt5:bars:{sym}:{res} (TTL 30d) · mt5:quote:{sym}
  │               PUBLISH mt5:tick:{sym}
  ├─ PostgreSQL writes: bars_{1m|3m|5m|15m|60m}  ON CONFLICT DO NOTHING
  └─ /api/history read: Redis → PG → MT5 fallback
       └─ Weekend fallback: empty window near "now" → return last ~500 bars before to_ms
            (prevents noData:true blanking chart on weekends for XAUUSD/USOIL)
  ▼
WebSocket /ws/{sym}/{res}  (Redis pub/sub → broadcast to clients)
  ▼
datafeed.ts → TradingView widget  [container mt5_frontend :5173 — bind-mounted, HMR]
```

**Key invariants:**
- Frontend never talks to MT5. Worker never touches Redis/PG directly.
- Realtime = Redis pub/sub → WebSocket. History = Redis → PG → MT5. These are separate paths — one can be dead while the other looks healthy.
- Bar `time` is **milliseconds** everywhere.
- Symbols: `XAUUSD` `BTCUSD` `USOIL`. Resolutions: `1` `3` `5` `15` `60`.

---

## STEP 3 — Deploy commands (one place, use these)

```bash
# Backend change (backend/ or analysis/) — rebuild required
podman compose build server && podman compose up -d server

# Frontend change — no action needed, Vite HMR picks it up on save

# Worker change — restart the host process
python worker.py

# Bridge change (claude_bridge/) or after reboot
./start.ps1          # idempotent — skips what's already running; re-syncs WSL IP
./start.ps1 -Background   # headless; stop with: Get-Content .pids | Stop-Process
```

---

## Reference: File Map

```
.env                       ← secrets + AI_BACKEND (api|local) + LOCAL_CLAUDE_URL
docker-compose.yml         ← 4 Podman services: redis, postgres, server, frontend
start.ps1                  ← starts bridge + worker on host; syncs WSL gateway IP → .env

worker.py                  ← host: MT5 poll loop + startup backfill
claude_bridge/service.py   ← host: POST /analyze wraps `claude -p` (port 8088)

backend/                   ← FastAPI package (baked into server image)
  main.py                  ← app factory, lifespan, include_router, CORS
  core/config.py           ← env vars + constants (AI_BACKEND, AI_MODEL, etc.)
  core/state.py            ← redis/pg/ai_client/ws_manager singletons
  db/postgres.py           ← schema init + queries (bars_{1m|3m|5m|15m|60m})
  ai/analyzer.py           ← _call_claude() — routes to API or bridge per AI_BACKEND
  ai/prompt.py             ← _AI_SYSTEM + _build_ai_user_prompt
  ai/monitor.py            ← auto-analyze background loop
  analysis/market_structure.py  ← ms_detect() [ZigZag→HH/HL→BOS/CHOCH] — ACTIVE
  analysis/elliott.py      ← DEAD CODE — not imported anywhere, ignore

frontend/src/
  lib/datafeed.ts          ← IBasicDataFeed: getBars (HTTP) + subscribeBars (WS)
  components/Chart/Chart.tsx    ← TV widget init (useRef + useEffect once)
  hooks/useMarketStructure.ts   ← TV shape drawing (BOS/CHOCH/channel)
  hooks/useAiWs.ts         ← /ws/ai/{symbol} → AICard[] state
  types/api.ts             ← Bar, AICard, Resolution, EWResult (API shapes)
  types/domain.ts          ← SYMBOLS, TIMEFRAMES, AI_RESOLUTIONS constants
```

> Deep file details → load the relevant sub-skill. This map is for orientation only.
> Infrastructure deep-dive (Redis keyspace, PG tables, network, env wiring, failure-mode diagnosis) → `reference/infrastructure.md` in this skill.

---

## Reference: API Endpoints

| Kind | Path | Purpose |
|------|------|---------|
| WS  | `/ws/{symbol}/{resolution}` | Realtime bars → `onTick`. Live bar on connect, then pub/sub |
| WS  | `/ws/ai/{symbol}` | AI analysis push — cached results on connect, then pub/sub |
| GET | `/api/history?symbol=&resolution=&from=&to=` | Redis → PG → MT5 fallback chain |
| GET | `/api/quote?symbol=` | `{bid, ask, time}` |
| GET | `/api/ms?symbol=&resolution=` | Cached Market Structure (5min TTL) |
| POST| `/api/ms/compute?symbol=&resolution=&bars=` | Run MS detector, cache 5min |
| GET | `/api/ai_analysis?symbol=&resolution=` | Read cached AI result — does NOT call Claude |
| POST| `/api/ai/analyze?symbol=[&resolution=][&force=]` | Trigger Claude analysis |
| GET | `/api/config` · `/api/symbols` · `/api/resolve` | TradingView datafeed config |
| POST| `/webhook/tick/{symbol}` | Worker → live/closed/quote (Bearer auth) |
| POST| `/webhook/bars/batch` | Worker backfill (Bearer auth) |

Webhooks: `Authorization: Bearer ${WEBHOOK_SECRET}`. Browser endpoints: open (CORS `*`).

---

## Reference: Gotchas (symptom → diagnosis → fix)

### Realtime candles frozen — forming bar not updating
History reads still work, so it can look healthy. Suspect `_pubsub_listener` is dead.
```bash
podman exec mt5_redis redis-cli PUBSUB NUMSUB mt5:tick:BTCUSD   # must be ≥ 1
```
If 0: restart server (`podman compose up -d server`).

### Chart blank on weekends — price axis shows wrong symbol's range (e.g. BTC price on XAUUSD)
Not a data issue. XAUUSD/USOIL sessions are Mon–Fri; TV's first `getBars` asks for a window near "now" (weekend) → gets `noData:true` → stops paging backward → blank chart.
The weekend fallback in `/api/history` handles this (returns last ~500 bars before `to_ms` instead of `noData`).
Diagnosis: `curl "localhost:8000/api/history?symbol=XAUUSD&resolution=60&from=<now-3h>&to=<now>"` — should NOT return `noData:true` if `ZCARD mt5:bars:XAUUSD:60` > 0.

### AI analyze timeout / 504 (AI_BACKEND=local)
Check in order:
1. Bridge running on host? `Invoke-RestMethod http://127.0.0.1:8088/health`
2. Container can reach bridge? `podman exec mt5_server python -c "import os,urllib.request;urllib.request.urlopen(os.environ['LOCAL_CLAUDE_URL']+'/health')"`
3. IP stale after reboot? Run `./start.ps1` — it re-syncs WSL gateway IP into `.env` + recreates server container if IP changed.

> **GOTCHA:** `host.containers.internal` does NOT resolve correctly on Podman/Windows — it points to `10.89.0.1` (the Podman VM bridge), not the Windows host. Must use WSL gateway IP. `start.ps1` handles this automatically.

### AI analyze slow (~6–18s per call)
Expected. Each call spawns a `claude.exe` process (~431MB). Bottleneck is CLI startup, not concurrency. `BRIDGE_CONCURRENCY=8` prevents queue buildup during monitor fan-out but won't speed individual calls.
Check which path is active: `token_stats.model` in response — `local:sonnet` = bridge, `claude-*` = API.

### Backend edit has no effect
Image not rebuilt. Run `podman compose build server && podman compose up -d server`.

### PG writes silently skipped
`asyncpg` not installed. Verify `requirements.server.txt` has `asyncpg>=0.29.0`.

### TV widget API calls throw / shapes don't appear
`createShape`/`createMultipointShape` called before `onChartReady` fires. Check `chartReadyRef` guard in `useMarketStructure.ts`.
Also: `chart.removeEntity(id)` can throw if entity already removed — always wrap in try/catch.

### User position lock not detected / not syncing
Lock state is `props.frozen`, NOT `props.lock`. Subscribe to `drawing_event` (fires on lock as `properties_changed`). `onAutoSaveNeeded` does NOT fire on lock — keep it only as a fallback.
`stopLevel`/`profitLevel` are in **ticks**, not price. Convert: `priceOffset = level * (minmov / pricescale)`.

### `window.TradingView` is undefined
`charting_library.standalone.js` must load **synchronously** before `<script type="module">` in `index.html`. Moving it or making it async breaks this silently.

### TypeScript errors not caught in dev
Vite dev mode does NOT type-check. Run `tsc --noEmit` separately to surface errors.

### React StrictMode double-mounts causing duplicate widget / double WS subscription
Cleanup function in `useEffect` is mandatory. Call `datafeed.closeAll()` and `widget.remove()` in the cleanup return.

---

## Reference: Sub-skill Index

| Skill | Load when... |
|---|---|
| `react-typescript` | React/TS components, hooks, datafeed, TV widget in React lifecycle, actual file structure |
| `tradingview-setup` | TV widget options, datafeed API spec, overrides, featuresets |
| `technical-analysis` | MS/BOS/CHOCH pipeline, AI prompt/signal, SL/TP/R:R logic, debug AI signal |
| `python-backend` | FastAPI structure, tách module, refactor, circular import, lifespan/background task |
| `run` (global) | Launch app, screenshot, test golden path |
| `verify` (global) | Confirm a change works end-to-end |
| `code-review` (global) | Review diff before committing |

---

## Reference: Agent Briefing Template

```
## Project context
Windows 11 MT5 trading chart. Realtime pipeline (NOT HTTP polling):
- worker.py: Windows host, polls MT5 every 1s → POST /webhook/tick/{symbol} (Bearer auth). Backfills on start.
- FastAPI backend: container mt5_server :8000 (baked image — changes need rebuild).
  Writes Redis (hot cache + pub/sub) + PostgreSQL (bars_{1m|3m|5m|15m|60m}).
  History read: Redis → PG → MT5 fallback. Realtime: Redis pub/sub → WebSocket.
- Redis :6379 (no persistence, 256mb LRU). PostgreSQL :5432 db=trading user=trader.
- Frontend: container mt5_frontend :5173, Vite + React + TS, bind-mounted (HMR).
  TradingView Advanced Charts v31.2.0 as window.TradingView.
  Key files: Chart.tsx (widget), datafeed.ts (history+WS), useMarketStructure.ts (shapes).
- AI: backend/ai/analyzer.py _call_claude(). AI_BACKEND=api (Anthropic SDK) or local
  (bridge port 8088 on host). Container reaches bridge via WSL gateway IP —
  host.containers.internal does NOT work on Podman/Windows. start.ps1 syncs IP.
- Symbols: XAUUSD BTCUSD USOIL. Resolutions: 1 3 5 15 60. Bar time = milliseconds.
- Project root: C:\Users\namnt\Poc\trading\

## Sub-skills available
react-typescript · tradingview-setup · technical-analysis · python-backend

## Task
<describe here>
```
