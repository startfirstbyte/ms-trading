---
name: react-typescript
description: >
  Skill mô tả chuẩn React + TypeScript đang dùng trong frontend của trading project.
  Load khi: tạo component mới, viết hook, sửa datafeed, xử lý TradingView widget
  trong React lifecycle, thêm type mới, debug TypeScript error, hay bất kỳ câu hỏi
  về cấu trúc frontend hiện tại.
---

# React + TypeScript Frontend — Trạng thái hiện tại

Frontend **đã migrate xong** sang React + TypeScript. Không còn `main.js` hay `datafeed.js`.

---

## 1. Cấu trúc file thực tế

```
frontend/
  index.html              ← standalone.js SYNC trước <script type="module">
  vite.config.ts          ← middleware serve /charting_library/ + plugin react
  tsconfig.json           ← paths: charting_library + datafeed-api → ../package/
  src/
    main.tsx              ← createRoot, StrictMode
    App.tsx               ← symbol/resolution state, layout (app > toolbar + content)
    index.css             ← CSS vars + reset

    types/
      api.ts              ← tất cả API response types (xem mục 3)
      domain.ts           ← constants: SYMBOLS, TIMEFRAMES, VALID_*, EW_LOOKBACK,
                            AI_RESOLUTIONS, AI_AUTO_OPTIONS (xem mục 4)
      tv.d.ts             ← window.TradingView global declaration
      css-modules.d.ts    ← declare module '*.module.css'

    lib/
      api.ts              ← typed fetch wrapper + WS_BASE (xem mục 5)
      datafeed.ts         ← createDatafeed() → IBasicDataFeed + closeAll() (xem mục 6)
      utils.ts            ← misc helpers

    hooks/
      useUrlState.ts      ← symbol + resolution qua URL params + localStorage
      useMarketStructure.ts ← vẽ TV shapes (channel, BOS/CHOCH, swing labels)
      useAiWs.ts          ← WebSocket /ws/ai/{symbol} → AICard[] state
      useQuote.ts         ← polls /api/quote mỗi 3s

    components/
      Chart/              Chart.tsx + Chart.module.css
      Toolbar/            Toolbar.tsx + Toolbar.module.css
      AiDashboard/        AiDashboard.tsx + AiDashboard.module.css
      QuoteTicker/        QuoteTicker.tsx + QuoteTicker.module.css
      ui/                 card.tsx  badge.tsx  separator.tsx
```

---

## 2. Setup (đã có, chỉ tham khảo)

### tsconfig.json — paths alias trỏ vào ../package/

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "paths": {
      "charting_library": ["../package/charting_library.d.ts"],
      "datafeed-api":     ["../package/datafeed-api.d.ts"]
    }
  },
  "include": ["src"]
}
```

### vite.config.ts — middleware serve charting library

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PACKAGE_DIR = path.resolve(__dirname, '../package')
const MIME: Record<string, string> = {
  '.js': 'application/javascript', '.css': 'text/css',
  '.json': 'application/json', '.woff2': 'font/woff2',
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'serve-charting-library',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (!req.url?.startsWith('/charting_library/')) return next()
          const rel      = req.url.slice('/charting_library/'.length).split('?')[0]
          const filePath = path.join(PACKAGE_DIR, rel)
          if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
            res.setHeader('Content-Type', MIME[path.extname(filePath)] ?? 'application/octet-stream')
            fs.createReadStream(filePath).pipe(res)
          } else next()
        })
      },
    },
  ],
  server: { port: 5173 },
})
```

### tv.d.ts — window.TradingView global

```ts
/// <reference path="../../../package/charting_library.d.ts" />
/// <reference path="../../../package/datafeed-api.d.ts" />
import type { ChartingLibraryWidgetConstructor } from 'charting_library'

declare global {
  interface Window {
    TradingView: { widget: ChartingLibraryWidgetConstructor }
  }
}
```

### index.html — thứ tự load quan trọng

```html
<head>
  <!-- SYNC trước module — thiếu dấu async/defer -->
  <script src="/charting_library/charting_library.standalone.js"></script>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/src/main.tsx"></script>
</body>
```

---

## 3. Types — `src/types/api.ts`

```ts
export interface Bar {
  time: number    // milliseconds UTC
  open: number; high: number; low: number; close: number; volume: number
}
export interface HistoryResponse { bars: Bar[]; noData: boolean }
export interface QuoteResponse   { bid: number; ask: number; time: number }
export interface SymbolItem      { name: string; description: string }
export type Resolution = '1' | '3' | '5' | '15' | '60'

export interface EWWave   { label: string; time: number; price: number; type?: 'high' | 'low' }
export interface EWChannel {
  upper: number; lower: number; mid: number; direction: 'up' | 'down' | 'flat'
  upper_start: number; lower_start: number; upper_end: number; lower_end: number
  time_start: number; time_end: number
}
export interface EWTarget  { ratio: number; price: number }
export interface EWResult  {
  pattern: string; direction: 'bullish' | 'bearish' | 'neutral'
  prediction: 'up' | 'down' | 'flat'; confidence: number; complete: boolean
  waves: EWWave[]; targets: EWTarget[]; next_target: number | null
  channel?: EWChannel
  structure?: { trend: string; bos_level: number | null; event: string; swings: EWWave[] }
}
export interface AICard {
  signal: 'BUY' | 'SELL' | 'WAIT'; conviction: 'HIGH' | 'MEDIUM' | 'LOW'
  entry_zone: number | null; target: number | null; stop_loss: number | null
  analysis: string; timestamp_ms: number; resolution: string
  elliott_pattern?: string; elliott_confidence?: number; analysis_bid?: number | null
}
```

---

## 4. Domain constants — `src/types/domain.ts`

```ts
export const SYMBOLS = [
  { name: 'XAUUSD', label: 'XAU/USD' },
  { name: 'BTCUSD', label: 'BTC/USD' },
  { name: 'USOIL',  label: 'OIL' },
]
export const TIMEFRAMES = [
  { label: '1m', resolution: '1' }, { label: '3m', resolution: '3' },
  { label: '5m', resolution: '5' }, { label: '15m', resolution: '15' },
  { label: '1H', resolution: '60' },
]
export const VALID_SYMBOLS:     string[] = SYMBOLS.map(s => s.name)
export const VALID_RESOLUTIONS: string[] = TIMEFRAMES.map(t => t.resolution)
export const EW_LOOKBACK: Record<string, number> = {
  '1': 120, '3': 100, '5': 100, '15': 80, '60': 300,
}
export const AI_RESOLUTIONS: Resolution[] = ['1', '3', '5', '15', '60']
export const AI_AUTO_OPTIONS = [
  { label: 'Auto: Tắt',    ms: 0 },
  { label: 'Auto: 1 phút', ms: 60_000 },
  { label: 'Auto: 3 phút', ms: 180_000 },
  { label: 'Auto: 5 phút', ms: 300_000 },
  { label: 'Auto: 15 phút', ms: 900_000 },
  { label: 'Auto: 1 giờ',  ms: 3_600_000 },
]
```

---

## 5. API Client — `src/lib/api.ts`

```ts
const BASE = 'http://localhost:8000/api'
export const WS_BASE = 'ws://localhost:8000/ws'

async function get<T>(endpoint: string, params: Record<string, string | number> = {}): Promise<T> {
  const qs  = new URLSearchParams(Object.entries(params).map(([k,v]) => [k, String(v)])).toString()
  const res = await fetch(`${BASE}/${endpoint}${qs ? '?' + qs : ''}`)
  if (!res.ok) throw new Error(`${endpoint} → ${res.status}`)
  return res.json() as Promise<T>
}
async function post<T>(endpoint: string, params: Record<string, string | number> = {}): Promise<T> {
  const qs  = new URLSearchParams(Object.entries(params).map(([k,v]) => [k, String(v)])).toString()
  const res = await fetch(`${BASE}/${endpoint}${qs ? '?' + qs : ''}`, { method: 'POST' })
  if (!res.ok) throw new Error(`${endpoint} → ${res.status}`)
  return res.json() as Promise<T>
}

export const api = {
  config:          ()                               => get('config'),
  symbols:         (query = '')                     => get<SymbolItem[]>('symbols', { query }),
  resolve:         (symbol: string)                 => get<Record<string, unknown>>('resolve', { symbol }),
  history:         (symbol, resolution, from, to)   => get<HistoryResponse>('history', { symbol, resolution, from, to }),
  quote:           (symbol: string)                 => get<QuoteResponse>('quote', { symbol }),
  elliottCompute:  (symbol, resolution, bars)       => post('elliott/compute', { symbol, resolution, bars }),
  aiAnalysisCache: (symbol, resolution)             => get('ai_analysis', { symbol, resolution }),
  aiAnalyze:       (symbol: string, force = false)  => post('ai/analyze', { symbol, force }),
}
```

---

## 6. Datafeed — `src/lib/datafeed.ts`

Trả về `IBasicDataFeed & { closeAll: () => void }` — phải gọi `closeAll()` khi widget bị destroy.

```ts
export function createDatafeed(): IBasicDataFeed & { closeAll: () => void } {
  const subs = new Map<string, { ws: WebSocket; pingTimer: ReturnType<typeof setInterval> }>()

  return {
    onReady(callback) {
      api.config()
        .then(cfg => setTimeout(() => callback({ ...cfg, supports_time: true }), 0))
        .catch(() => setTimeout(() => callback({ supported_resolutions: ['1','3','5','15','60'], supports_time: true }), 0))
    },

    getServerTime(callback) { callback(Math.round(Date.now() / 1000)) },

    // searchSymbols, resolveSymbol, getBars — gọi api.* rồi map sang TV types

    subscribeBars(symbolInfo, resolution, onTick, uid, onResetCacheNeededCallback) {
      const ws = new WebSocket(`${WS_BASE}/${symbolInfo.name}/${resolution}`)
      ws.onmessage = (e) => {
        try { const bar = JSON.parse(e.data); bar.time = Math.trunc(bar.time); onTick(bar) } catch {}
      }
      ws.onclose = (e) => { if (e.code !== 1000) onResetCacheNeededCallback?.() }
      const pingTimer = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('ping') }, 30_000)
      subs.set(uid, { ws, pingTimer })
    },

    unsubscribeBars(uid) {
      const sub = subs.get(uid)
      if (!sub) return
      clearInterval(sub.pingTimer); sub.ws.close(1000); subs.delete(uid)
    },

    closeAll() {
      for (const { ws, pingTimer } of subs.values()) { clearInterval(pingTimer); ws.close(1000) }
      subs.clear()
    },
  }
}
```

---

## 7. Chart Component — `src/components/Chart/Chart.tsx`

Pattern chuẩn: **init widget một lần** (`[]` deps), `setSymbol` khi symbol/resolution đổi.

```tsx
export function Chart({ symbol, resolution }: Props) {
  const containerRef  = useRef<HTMLDivElement>(null)
  const widgetRef     = useRef<IChartingLibraryWidget | null>(null)
  const chartReadyRef = useRef<boolean>(false)
  const datafeedRef   = useRef<ReturnType<typeof createDatafeed> | null>(null)

  const { chip, redraw, clearShapes } = useMarketStructure(widgetRef, chartReadyRef, symbol, resolution)
  const redrawRef = useRef(redraw); redrawRef.current = redraw
  const clearRef  = useRef(clearShapes); clearRef.current = clearShapes

  // Init widget ONCE
  useEffect(() => {
    if (!containerRef.current || !window.TradingView) return
    const datafeed = createDatafeed()
    datafeedRef.current = datafeed
    widgetRef.current = new window.TradingView.widget({
      container: containerRef.current,
      library_path: '/charting_library/',
      datafeed, symbol, interval: resolution,
      locale: 'en', theme: 'Dark', autosize: true, timezone: 'Etc/UTC',
      disabled_features: ['header_compare','header_undo_redo','header_saveload',
        'use_localstorage_for_settings','header_account_manager',
        'trading_account_manager','show_right_widgets_panel_by_default'],
      enabled_features: ['hide_left_toolbar_by_default'],
      overrides: { ...WIDGET_OVERRIDES, 'mainSeriesProperties.showCountdown': true },
    })
    widgetRef.current.onChartReady(() => {
      chartReadyRef.current = true
      redrawRef.current()     // draw shapes after chart ready
    })
    return () => {
      chartReadyRef.current = false
      datafeed.closeAll()
      try { widgetRef.current?.remove() } catch {}
      widgetRef.current = datafeedRef.current = null
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Symbol/resolution change — không destroy widget
  useEffect(() => {
    const widget = widgetRef.current
    if (!widget || !chartReadyRef.current) return
    clearRef.current()
    widget.setSymbol(symbol, resolution, () => redrawRef.current())
  }, [symbol, resolution])

  return <div ref={containerRef} className={styles.chart} />
}
```

---

## 8. Hook Pattern — useMarketStructure

`useMarketStructure(widgetRef, chartReadyRef, symbol, resolution)` → `{ chip: MSChip, redraw, clearShapes }`

- `chip` — `{ text: string; cls: '' | 'up' | 'down' }` — dùng để hiển thị badge trên Toolbar
- `redraw()` — clear shapes + fetch lại từ server + vẽ lại
- `clearShapes()` — chỉ xoá shapes, không fetch

Khi tạo hook mới liên quan đến TV chart, luôn nhận `widgetRef: RefObject<IChartingLibraryWidget | null>` và `chartReadyRef: RefObject<boolean>` làm params — guard trước khi gọi `widget.chart()`.

---

## 9. CSS Pattern

**CSS Modules** cho từng component (`*.module.css`). Global vars trong `src/index.css`:

```css
:root {
  --bg:      #131722;
  --surface: #1e222d;
  --border:  #2a2e39;
  --text:    #d1d4dc;
  --muted:   #787b86;
  --accent:  #2962ff;
  --green:   #26a69a;
  --red:     #ef5350;
}
html, body, #root { height: 100%; overflow: hidden; background: var(--bg); color: var(--text); }
.app { display: flex; flex-direction: column; height: 100vh; }
.content { display: flex; flex: 1; min-height: 0; }
```

---

## 10. Checklist trước khi viết code

- [ ] `createDatafeed()` gọi **bên trong** `useEffect`, không ở module scope
- [ ] `useEffect(fn, [])` cho widget init — không deps để tránh remount
- [ ] Cleanup: `datafeed.closeAll()` + `widget?.remove()` + null refs
- [ ] Mọi `chart()` API call phải đằng sau `chartReadyRef.current === true`
- [ ] `chart.removeEntity()` wrap trong try/catch
- [ ] `useEffect` có cleanup nếu tạo subscription / timer / AbortController
- [ ] Không có `any` — dùng `unknown` + type guard hoặc explicit cast với comment
- [ ] CSS class names qua `styles.xxx` (CSS Modules)
- [ ] Import từ `charting_library` (widget types) hay `datafeed-api` (datafeed types) — không import từ relative path

---

## 11. Gotchas React + TradingView

| Vấn đề | Nguyên nhân | Fix |
|--------|-------------|-----|
| Blank chart, no error | `window.TradingView` undefined | `standalone.js` load order sai trong `index.html` |
| Shapes vẽ 2 lần (dev) | React StrictMode double-invoke effect | Cleanup function đủ để handle — đừng remove StrictMode |
| `chart()` throws | Gọi trước `onChartReady` | Guard bằng `chartReadyRef.current` |
| Old shapes còn sau khi đổi symbol | `clearShapes()` không được gọi | Gọi `clearRef.current()` trước `setSymbol` |
| Datafeed WS leak | `createDatafeed()` ở ngoài `useEffect` | Luôn tạo trong `useEffect`, `closeAll()` trong cleanup |
| Vite không báo TS error | Vite dùng esbuild — không type-check | Chạy `tsc --noEmit` để kiểm tra type errors |
| `setSymbol` không nhận ms interval | TV interval là string, không phải number | Truyền `resolution` dưới dạng string `'1'` không phải `1` |
| Bars time violation warning | `subscribeBars` gửi bar cũ hơn bar hiện tại | Chỉ `onTick` với bar có `time >= lastBar.time` |
