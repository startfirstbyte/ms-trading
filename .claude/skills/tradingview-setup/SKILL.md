---
name: tradingview-setup
description: Reference skill for TradingView Advanced Charts (Charting Library) integration in this project. Use when investigating chart setup, datafeed wiring, widget options, overrides, or featuresets. Covers the local package at ../package/ and this project's FastAPI + Vite architecture.
---

# TradingView Advanced Charts — Project Reference

Official docs: https://www.tradingview.com/charting-library-docs/latest/quick-start/

---

## Project Layout

```
trading/
  package/                        ← TV library v31.2.0 (private, do not redistribute)
    charting_library.standalone.js   standalone IIFE (used in this project)
    charting_library.esm.js          ES module
    charting_library.cjs.js          CommonJS
    charting_library.d.ts            widget TypeScript types
    datafeed-api.d.ts                Datafeed interface types
    bundles/                         lazy-loaded chunks (must be co-served)
  server.py                       ← FastAPI backend, serves MT5 data at :8000
  frontend/
    vite.config.js                ← serves ../package/* at /charting_library/ in dev
    index.html                    ← loads standalone.js via <script>, then ESM main
    src/
      main.js                     ← creates TradingView.widget, manages symbol/tf
      datafeed.js                 ← implements Datafeed API against /api/*
      style.css                   ← dark theme matching TV Dark
```

---

## Starting the Project

```bash
# Terminal 1 — backend
cd C:\Users\namnt\Poc\trading
python -m uvicorn server:app --host 0.0.0.0 --port 8000

# Terminal 2 — frontend
cd C:\Users\namnt\Poc\trading\frontend
npm run dev          # → http://localhost:5173
```

---

## Serving the Library (Vite Dev)

The `package/` dir is served at `/charting_library/` via a custom Vite middleware in `vite.config.js`.
The widget is initialized with `library_path: '/charting_library/'` so the browser loads bundles from there.

In `index.html`, the standalone script must be a **sync `<script>`** tag loaded **before** the ES module:
```html
<script src="/charting_library/charting_library.standalone.js"></script>
<script type="module" src="/src/main.js"></script>
```
This creates `window.TradingView` before the module runs.

---

## Widget Constructor (key options)

```js
new window.TradingView.widget({
  container:    document.getElementById('chart'), // or string ID
  library_path: '/charting_library/',             // MUST end with /
  datafeed:     Datafeed,                         // object implementing Datafeed API
  symbol:       'XAUUSD',
  interval:     '60',                             // resolution string (see below)
  locale:       'en',
  theme:        'Dark',                           // 'Dark' | 'Light'
  autosize:     true,
  timezone:     'Etc/UTC',
  disabled_features: [...],
  enabled_features:  [...],
  overrides:    {...},
})
```

Full options ref: https://www.tradingview.com/charting-library-docs/latest/api/interfaces/Charting_Library.ChartingLibraryWidgetOptions/

---

## Resolution Strings

| Label | Resolution string | Seconds |
|-------|------------------|---------|
| 1 min | `'1'`            | 60      |
| 3 min | `'3'`            | 180     |
| 5 min | `'5'`            | 300     |
| 15 min| `'15'`           | 900     |
| 1 hour| `'60'`           | 3600    |
| 4 hour| `'240'`          | 14400   |
| 1 day | `'1D'`           | —       |
| 1 week| `'1W'`           | —       |

---

## Datafeed API

Docs: https://www.tradingview.com/charting-library-docs/latest/connecting_data/Datafeed-API/

All methods that use callbacks **must call them in a separate MacroTask** (`setTimeout(..., 0)`) to avoid stack overflow.

### onReady(callback)
Called on init. Pass a `DatafeedConfiguration` object:
```js
{
  supported_resolutions: ['1','3','5','60'],
  supports_search: true,
  supports_group_request: false,
  supports_marks: false,
  supports_timescale_marks: false,
}
```

### searchSymbols(userInput, exchange, symbolType, onResultReadyCallback)
Return array of `{ symbol, full_name, description, exchange, type }`.

### resolveSymbol(symbolName, onSymbolResolvedCallback, onResolveErrorCallback)
Return `LibrarySymbolInfo` — see section below. Call `onResolveErrorCallback('unknown_symbol')` on failure.

### getBars(symbolInfo, resolution, periodParams, onHistoryCallback, onErrorCallback)
- `periodParams`: `{ from, to, countBack, firstDataRequest }` — `from`/`to` are Unix seconds
- Bars **must be in ascending chronological order**
- Return at least `countBack` bars when possible (may go earlier than `from`)
- `onHistoryCallback(bars, { noData: true })` when no older data exists

### subscribeBars(symbolInfo, resolution, onRealtimeCallback, listenerGuid, onResetCacheNeededCallback)
- **Realtime is a WebSocket, not HTTP polling.** `datafeed.js` opens
  `ws://localhost:8000/ws/{symbol}/{resolution}`, and on each frame calls
  `onRealtimeCallback(bar)` (after `bar.time = Math.trunc(bar.time)`).
- The server sends the cached live bar **on connect**, then streams updates pushed
  from Redis pub/sub. Same `time` as last bar → updates the forming candle; greater
  `time` → appends a new bar.
- Can only update the **most recent bar** or append newer bars — never modify history
  (`_putToCacheNewBar: time violation`).
- A 30s `ping` keepalive runs; `unsubscribeBars(listenerGuid)` closes the socket
  (code 1000). Non-1000 close fires `onResetCacheNeededCallback` so the library refetches.
- **If the forming candle stops updating but history loads, the bug is the server's
  Redis pub/sub listener, not the chart.** Check `PUBSUB NUMSUB mt5:tick:{symbol}` ≥ 1.

### unsubscribeBars(listenerGuid)
Stop streaming for `listenerGuid`.

---

## LibrarySymbolInfo Fields

```js
{
  name:                 'XAUUSD',
  description:          'Gold vs USD',
  type:                 'forex',            // forex | crypto | stock | commodity | futures
  session:              '24x7',             // or e.g. '0930-1600' for NYSE
  timezone:             'Etc/UTC',
  exchange:             'MT5',
  minmov:               1,
  pricescale:           100,                // 100 = 2 decimal places, 1000 = 3
  has_intraday:         true,
  supported_resolutions: ['1','3','5','60'],
  volume_precision:     0,
  data_status:          'streaming',        // streaming | endofday | delayed_streaming
}
```

`pricescale` from MT5: `int(round(1 / symbol_info.point))` — the `server.py /api/resolve` endpoint computes this automatically.

---

## Key Overrides

```js
overrides: {
  // Candles
  'mainSeriesProperties.candleStyle.upColor':          '#26a69a',
  'mainSeriesProperties.candleStyle.downColor':        '#ef5350',
  'mainSeriesProperties.candleStyle.borderUpColor':    '#26a69a',
  'mainSeriesProperties.candleStyle.borderDownColor':  '#ef5350',
  'mainSeriesProperties.candleStyle.wickUpColor':      '#26a69a',
  'mainSeriesProperties.candleStyle.wickDownColor':    '#ef5350',

  // Background & grid
  'paneProperties.background':                         '#131722',
  'paneProperties.backgroundType':                     'solid',
  'paneProperties.horzGridProperties.color':           '#1e222d',
  'paneProperties.vertGridProperties.color':           '#1e222d',

  // Price scale
  'scalesProperties.textColor':                        '#d1d4dc',
  'scalesProperties.lineColor':                        '#2a2e39',
  'scalesProperties.backgroundColor':                  '#131722',
}
```

---

## Commonly Used Featuresets

### disabled_features
| Flag | Effect |
|------|--------|
| `header_symbol_search` | Removes the symbol search box from the header |
| `header_compare`        | Removes the compare overlay button |
| `header_undo_redo`      | Removes undo/redo buttons |
| `header_saveload`       | Removes save/load layout buttons |
| `timeframes_toolbar`    | Removes bottom timeframe bar |
| `context_menus`         | Disables right-click menus |
| `use_localstorage_for_settings` | Prevents chart from caching settings in localStorage |

### enabled_features
| Flag | Effect |
|------|--------|
| `hide_left_toolbar_by_default` | Collapses drawing tools sidebar on load |
| `create_volume_indicator_by_default` | Adds Volume indicator automatically |
| `show_spread_operators` | Allows spread expressions in symbol search |

Full list: https://www.tradingview.com/charting-library-docs/latest/customization/Featuresets/

---

## Backend API Endpoints (server.py)

Used by the datafeed (history is HTTP, realtime is WebSocket):

| Method | Path | Params | Returns |
|--------|------|--------|---------|
| GET | `/api/config` | — | Datafeed configuration object |
| GET | `/api/symbols` | `query=` | List of `{name, description}` |
| GET | `/api/resolve` | `symbol=` | `LibrarySymbolInfo` |
| GET | `/api/history` | `symbol`, `resolution`, `from`, `to` | `{bars, noData}` (closed bars + appended live bar) |
| GET | `/api/quote`   | `symbol=` | `{bid, ask, time}` |
| WS  | `/ws/{symbol}/{resolution}` | — | realtime bar frames → `onTick` |
| WS  | `/ws/ai/{symbol}` | — | realtime AI analysis push |

Data is fed in by `worker.py` (host) via `POST /webhook/tick/{symbol}` → Redis →
pub/sub → the WebSocket above. The datafeed does **not** poll for realtime.

Resolution↔MT5 timeframe map in `server.py` (kept in sync with `worker.py`):
```python
TIMEFRAME_MAP = { '1': TIMEFRAME_M1, '3': TIMEFRAME_M3, '5': TIMEFRAME_M5,
                  '15': TIMEFRAME_M15, '60': TIMEFRAME_H1 }
```

---

## Common Pitfalls

1. **`library_path` must end with `/`** — omitting the slash breaks bundle loading.
2. **Bars must be ascending** — descending order causes silent rendering bugs.
3. **Callbacks must be async** — call onReady/resolveSymbol callbacks in `setTimeout(..., 0)`.
4. **`subscribeBars` cannot edit history** — only the latest bar can be updated; modifying older bars triggers `time violation`.
5. **`countBack` in getBars** — the library may request more bars than fit in `[from, to]`; always try to return `countBack` bars.
6. **Standalone JS must load before ESM module** — in `index.html`, the `<script src="...standalone.js">` must come before `<script type="module">`.
