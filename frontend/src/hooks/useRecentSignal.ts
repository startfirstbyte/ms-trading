import { useEffect, useState } from 'react'
import type { AICard, Signal } from '../types/api'

// Remembers the last BUY/SELL per timeframe so a signal that flips back to WAIT
// on the next analysis is not lost — the user can still see it (and whether the
// price is still in the actionable zone). Persisted to localStorage for reloads.

export interface RecentSignal {
  signal:    Signal
  entry:     number | null
  watchBuy:  number | null
  watchSell: number | null
  target:    number | null
  stop:      number | null
  ts:        number          // ms — when the signal was produced
}

export function sigSide(sig: Signal | string | null | undefined): 'BUY' | 'SELL' | null {
  if (!sig) return null
  if (sig === 'BUY' || sig === 'BUY_LIMIT' || sig === 'BUY_STOP') return 'BUY'
  if (sig === 'SELL' || sig === 'SELL_LIMIT' || sig === 'SELL_STOP') return 'SELL'
  return null
}

export const RECENT_WINDOW_MS = 30 * 60_000   // keep showing for 30 min
const KEY = (sym: string) => `recentSignal:${sym}`

export function useRecentSignal(symbol: string, cards: Record<string, AICard>) {
  const [recent, setRecent] = useState<Record<string, RecentSignal>>({})

  // Reload remembered signals when the symbol changes
  useEffect(() => {
    try {
      const raw = localStorage.getItem(KEY(symbol))
      setRecent(raw ? (JSON.parse(raw) as Record<string, RecentSignal>) : {})
    } catch { setRecent({}) }
  }, [symbol])

  // Capture any BUY/SELL appearing in the cards
  useEffect(() => {
    setRecent(prev => {
      let changed = false
      const next = { ...prev }
      for (const [res, card] of Object.entries(cards)) {
        if (!card || !sigSide(card.signal)) continue
        const ts = card.timestamp_ms || Date.now()
        const ex = prev[res]
        if (!ex || ex.signal !== card.signal || ex.ts !== ts) {
          next[res] = {
            signal:    card.signal,
            entry:     card.entry_zone ?? null,
            watchBuy:  card.watch_buy  ?? null,
            watchSell: card.watch_sell ?? null,
            target:    card.target     ?? null,
            stop:      card.stop_loss  ?? null,
            ts,
          }
          changed = true
        }
      }
      if (!changed) return prev
      try { localStorage.setItem(KEY(symbol), JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }, [cards, symbol])

  return recent
}

export type RecentStatus = 'ok' | 'late' | 'gone'

// Is the recent signal still actionable given the live price?
export function recentStatus(rs: RecentSignal, bid: number | null): { label: string; kind: RecentStatus } {
  if (bid == null) return { label: 'Chờ giá', kind: 'late' }
  const { signal, target: tp, stop: sl } = rs
  const side  = sigSide(signal)
  const entry = side === 'BUY' ? (rs.entry ?? rs.watchBuy) : (rs.entry ?? rs.watchSell)

  if (side === 'BUY') {
    if (tp != null && bid >= tp) return { label: 'Đã cán TP — lỡ nhịp', kind: 'gone' }
    if (sl != null && bid <= sl) return { label: 'Đã cán SL — hết hiệu lực', kind: 'gone' }
    if (entry == null)           return { label: 'Còn hiệu lực', kind: 'ok' }
    if (bid <= entry * 1.0008)   return { label: 'Còn vào được', kind: 'ok' }   // tại/dưới entry
    return { label: 'Giá đã chạy lên — vào trễ', kind: 'late' }
  } else {
    if (tp != null && bid <= tp) return { label: 'Đã cán TP — lỡ nhịp', kind: 'gone' }
    if (sl != null && bid >= sl) return { label: 'Đã cán SL — hết hiệu lực', kind: 'gone' }
    if (entry == null)           return { label: 'Còn hiệu lực', kind: 'ok' }
    if (bid >= entry * 0.9992)   return { label: 'Còn vào được', kind: 'ok' }
    return { label: 'Giá đã chạy xuống — vào trễ', kind: 'late' }
  }
}
