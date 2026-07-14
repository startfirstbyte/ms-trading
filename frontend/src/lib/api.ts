import type { Bar, HistoryResponse, PriceChannel, QuoteResponse, Resolution, SymbolItem } from '../types/api'

const BASE = 'http://localhost:8000/api'

async function get<T>(endpoint: string, params: Record<string, string | number> = {}): Promise<T> {
  const qs = new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)])
  ).toString()
  const res = await fetch(`${BASE}/${endpoint}${qs ? '?' + qs : ''}`)
  if (!res.ok) throw new Error(`${endpoint} → ${res.status}`)
  return res.json() as Promise<T>
}

async function post<T>(endpoint: string, params: Record<string, string | number> = {}): Promise<T> {
  const qs = new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)])
  ).toString()
  const res = await fetch(`${BASE}/${endpoint}${qs ? '?' + qs : ''}`, { method: 'POST' })
  if (!res.ok) throw new Error(`${endpoint} → ${res.status}`)
  return res.json() as Promise<T>
}

async function postBody<T>(endpoint: string, params: Record<string, string>, body: unknown): Promise<T> {
  const qs = new URLSearchParams(params).toString()
  const res = await fetch(`${BASE}/${endpoint}${qs ? '?' + qs : ''}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${endpoint} → ${res.status}`)
  return res.json() as Promise<T>
}

export const api = {
  config: () =>
    get<{ supported_resolutions: Resolution[]; supports_time?: boolean }>('config'),

  symbols: (query = '') =>
    get<SymbolItem[]>('symbols', { query }),

  resolve: (symbol: string) =>
    get<Record<string, unknown>>('resolve', { symbol }),

  history: (symbol: string, resolution: Resolution, from: number, to: number) =>
    get<HistoryResponse>('history', { symbol, resolution, from, to }),

  quote: (symbol: string) =>
    get<QuoteResponse>('quote', { symbol }),

  msCompute: (symbol: string, resolution: string, bars: number) =>
    post<Record<string, unknown>>(`ms/compute`, { symbol, resolution, bars }),

  ms: (symbol: string, resolution: string) =>
    get<Record<string, unknown>>('ms', { symbol, resolution }),

  channels: (symbol: string, resolution: string) =>
    get<PriceChannel[]>('channels', { symbol, resolution }),

  chartStateLoad: (symbol: string) =>
    get<Record<string, unknown> | null>('chart_state', { symbol }),

  chartStateSave: (symbol: string, state: unknown) =>
    postBody<{ ok: boolean }>('chart_state', { symbol }, state),
}

export const WS_BASE = 'ws://localhost:8000/ws'
