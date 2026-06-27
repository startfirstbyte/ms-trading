import type {
  IBasicDataFeed,
  DatafeedConfiguration,
  LibrarySymbolInfo,
  SearchSymbolResultItem,
  PeriodParams,
  Bar,
  SubscribeBarsCallback,
  OnReadyCallback,
  ResolveCallback,
  ErrorCallback,
  HistoryCallback,
  SearchSymbolsCallback,
} from 'datafeed-api'

import { api, WS_BASE } from './api'
import type { Resolution } from '../types/api'

type Sub = {
  ws:          WebSocket
  pingTimer:   ReturnType<typeof setInterval>
  reconnTimer: ReturnType<typeof setTimeout> | null
  dead:        boolean   // set true when unsubscribeBars called → stop reconnecting
}

export function createDatafeed(): IBasicDataFeed & { closeAll: () => void } {
  const subs = new Map<string, Sub>()

  return {
    onReady(callback: OnReadyCallback) {
      api.config()
        .then(cfg =>
          setTimeout(
            () => callback({ ...cfg, supports_time: true } as DatafeedConfiguration),
            0
          )
        )
        .catch(() =>
          setTimeout(
            () =>
              callback({
                supported_resolutions: ['1', '3', '5', '15', '60'],
                supports_time: true,
              } as DatafeedConfiguration),
            0
          )
        )
    },

    getServerTime(callback: (time: number) => void) {
      callback(Math.round(Date.now() / 1000))
    },

    searchSymbols(
      userInput: string,
      _exchange: string,
      _type: string,
      onResult: SearchSymbolsCallback
    ) {
      api
        .symbols(userInput)
        .then(syms =>
          onResult(
            syms.map(
              s =>
                ({
                  symbol: s.name,
                  full_name: s.name,
                  description: s.description,
                  exchange: 'MT5',
                  type: 'forex',
                } as SearchSymbolResultItem)
            )
          )
        )
        .catch(() => onResult([]))
    },

    resolveSymbol(
      symbolName: string,
      onResolved: ResolveCallback,
      onError: ErrorCallback
    ) {
      api
        .resolve(symbolName)
        .then(info => setTimeout(() => onResolved(info as LibrarySymbolInfo), 0))
        .catch((err: Error) => onError(err.message))
    },

    async getBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: string,
      periodParams: PeriodParams,
      onHistory: HistoryCallback,
      onError: ErrorCallback
    ) {
      const { from, to } = periodParams
      try {
        const data = await api.history(
          symbolInfo.name,
          resolution as Resolution,
          from,
          to
        )
        if (data.noData || !data.bars?.length) {
          onHistory([], { noData: true })
        } else {
          onHistory(data.bars as Bar[], { noData: false })
        }
      } catch (e) {
        onError((e as Error).message)
      }
    },

    subscribeBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: string,
      onTick: SubscribeBarsCallback,
      uid: string,
      onResetCacheNeededCallback: () => void
    ) {
      let delay = 2_000   // reconnect backoff (ms), doubles each attempt, cap 30s

      const connect = () => {
        const sub = subs.get(uid)
        if (sub?.dead) return   // unsubscribeBars already called

        const url = `${WS_BASE}/${symbolInfo.name}/${resolution}`
        const ws  = new WebSocket(url)

        ws.onopen = () => {
          delay = 2_000   // reset backoff on successful connect
          const s = subs.get(uid)
          if (s) { s.ws = ws }
        }

        ws.onmessage = (event: MessageEvent) => {
          try {
            const bar = JSON.parse(event.data as string) as Bar
            bar.time = Math.trunc(bar.time)
            onTick(bar)
          } catch { /* ignore malformed frame */ }
        }

        ws.onerror = () => { /* error fires before close */ }

        ws.onclose = (e: CloseEvent) => {
          const s = subs.get(uid)
          if (!s || s.dead) return
          clearInterval(s.pingTimer)
          if (e.code !== 1000) {
            // Unexpected close → ask TV to reset bar cache once (gets fresh bars on reconnect)
            onResetCacheNeededCallback?.()
            const t = setTimeout(connect, Math.min(delay, 30_000))
            delay = Math.min(delay * 2, 30_000)
            s.reconnTimer = t
          }
        }

        const pingTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send('ping')
        }, 30_000)

        // Upsert sub record (preserve dead flag if it raced with unsubscribeBars)
        const existing = subs.get(uid)
        subs.set(uid, { ws, pingTimer, reconnTimer: existing?.reconnTimer ?? null, dead: existing?.dead ?? false })
      }

      // Placeholder so unsubscribeBars can mark dead before connect() fires
      subs.set(uid, { ws: null as unknown as WebSocket, pingTimer: null as unknown as ReturnType<typeof setInterval>, reconnTimer: null, dead: false })
      connect()
    },

    unsubscribeBars(uid: string) {
      const sub = subs.get(uid)
      if (!sub) return
      sub.dead = true
      if (sub.reconnTimer) clearTimeout(sub.reconnTimer)
      clearInterval(sub.pingTimer)
      try { sub.ws?.close(1000) } catch { /* ignore */ }
      subs.delete(uid)
    },

    closeAll() {
      for (const sub of subs.values()) {
        sub.dead = true
        if (sub.reconnTimer) clearTimeout(sub.reconnTimer)
        clearInterval(sub.pingTimer)
        try { sub.ws?.close(1000) } catch { /* ignore */ }
      }
      subs.clear()
    },
  }
}
