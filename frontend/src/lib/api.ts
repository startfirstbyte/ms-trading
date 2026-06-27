import type { AiPrediction, Bar, HistoryResponse, LosingStats, PriceChannel, QuoteResponse, Resolution, SymbolItem } from '../types/api'

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

export interface Winrate {
  wins:    number
  losses:  number
  pending: number
  decided: number
  rate:    number | null
}

// One LOCKED Long/Short position read off the chart
export interface UserPositionItem {
  shape_id:      string        // TradingView EntityId — stable identity within a session
  side:          'BUY' | 'SELL'
  entry:         number
  stop:          number | null
  target:        number | null
  entry_time_ms: number
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

  aiAnalysisCache: (symbol: string, resolution: string) =>
    get<Record<string, unknown>>('ai_analysis', { symbol, resolution }),

  aiAnalyze: (symbol: string, force = false, resolution?: string) =>
    post<unknown>('ai/analyze', resolution ? { symbol, force, resolution } : { symbol, force }),

  aiHistory: (symbol: string, resolution: string, limit = 10) =>
    get<AiPrediction[]>('ai_history', { symbol, resolution, limit }),

  winrate: (symbol: string, resolution: string) =>
    get<Winrate>('winrate', { symbol, resolution }),

  ms: (symbol: string, resolution: string) =>
    get<Record<string, unknown>>('ms', { symbol, resolution }),

  channels: (symbol: string, resolution: string) =>
    get<PriceChannel[]>('channels', { symbol, resolution }),

  losingTrades: (symbol: string) =>
    get<LosingStats>('losing_trades', { symbol }),

  aiReset: (symbol: string, resolution: string) =>
    post<{ ok: boolean }>('ai/reset', { symbol, resolution }),

  getAiAuto: () =>
    get<{ enabled: boolean }>('ai/auto', {}),

  setAiAuto: (enabled: boolean) =>
    post<{ enabled: boolean }>('ai/auto', { enabled: String(enabled) }),

  getTfConfig: (symbol: string) =>
    get<Record<string, boolean>>('ai/tf_config', { symbol }),

  setTfEnabled: (symbol: string, resolution: string, enabled: boolean) =>
    post<{ symbol: string; resolution: string; enabled: boolean }>(
      'ai/tf_enabled', { symbol, resolution, enabled: String(enabled) }
    ),

  userPositionSync: (symbol: string, resolution: string, positions: UserPositionItem[]) =>
    postBody<{ ok: boolean; persisted: number; open_count: number }>(
      'user_position', { symbol, resolution }, { positions }
    ),

  chartStateLoad: (symbol: string) =>
    get<Record<string, unknown> | null>('chart_state', { symbol }),

  chartStateSave: (symbol: string, state: unknown) =>
    postBody<{ ok: boolean }>('chart_state', { symbol }, state),
}

export const WS_BASE = 'ws://localhost:8000/ws'
