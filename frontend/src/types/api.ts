export interface Bar {
  time: number    // milliseconds UTC
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface HistoryResponse {
  bars: Bar[]
  noData: boolean
}

export interface QuoteResponse {
  bid: number
  ask: number
  time: number    // milliseconds UTC
}

export interface SymbolItem {
  name: string
  description: string
}

export type Resolution = '1' | '3' | '5' | '15' | '60'

export interface MSWave {
  label: string
  time: number    // milliseconds
  price: number
  type?: 'high' | 'low'
}

export interface MSChannel {
  upper: number
  lower: number
  mid: number
  slope_pct: number
  quality: number
  pos: number
  direction: 'up' | 'down' | 'flat'
  upper_start: number
  lower_start: number
  upper_end: number
  lower_end: number
  time_start: number   // milliseconds
  time_end: number     // milliseconds
  channel_type?: 'channel' | 'range' | 'none'
  width?: number       // biên độ kênh (upper - lower) theo giá
  width_pct?: number   // biên độ theo % so với mid
}

// Channel có vòng đời, lưu DB (/api/channels)
export interface PriceChannel {
  id: number
  status: 'editing' | 'committed'
  channel: MSChannel | null
  break_side: 'upper' | 'lower' | null
  break_price: number | null
  break_time: number | null    // milliseconds
  created_at: string | null
  committed_at: string | null
}

export interface MSTarget {
  ratio: number
  price: number
}

export interface MSWedgeLine {
  time_start:  number   // milliseconds
  price_start: number
  time_end:    number   // milliseconds
  price_end:   number
}

export interface MSWedge {
  type:      'rising' | 'falling' | 'symmetric'
  quality:   number
  apex_bars: number | null
  upper:     MSWedgeLine
  lower:     MSWedgeLine
}

export interface MSRuleSignal {
  signal: 'BUY' | 'SELL' | 'WAIT'
  pos:    number
  labels: string[]
}

export interface MSResult {
  pattern: string
  direction: 'bullish' | 'bearish' | 'neutral'
  prediction: 'up' | 'down' | 'flat'
  confidence: number
  complete: boolean
  scalp: boolean
  waves: MSWave[]
  draw_waves?: MSWave[]
  targets: MSTarget[]
  next_target: number | null
  channel?: MSChannel
  wedge?: MSWedge | null
  structure?: { trend: string; bos_level: number | null; event: string; swings: MSWave[] }
  rule_signal?: MSRuleSignal
}

// One past prediction row from /api/ai_history (PostgreSQL ai_predictions)
export type Signal = 'BUY' | 'BUY_LIMIT' | 'BUY_STOP' | 'SELL' | 'SELL_LIMIT' | 'SELL_STOP' | 'WAIT'

export interface AiPrediction {
  signal: Signal
  conviction: 'HIGH' | 'MEDIUM' | 'LOW' | null
  trigger: string | null
  analysis: string | null
  entry_zone: number | null
  target: number | null
  stop_loss: number | null
  key_level: number | null
  analysis_bid: number | null
  prediction_updated: boolean | null
  update_reason: string | null
  trade_status: 'HOLD' | 'CLOSE' | 'PARTIAL_TP' | null
  trade_note: string | null
  trigger_event: string | null
  created_at: string   // ISO 8601 timestamp (UTC)
}

export interface AICard {
  signal: Signal
  conviction: 'HIGH' | 'MEDIUM' | 'LOW'
  win_pct?: number | null
  trigger?: string
  watch_buy?: number | null
  watch_sell?: number | null
  key_level?: number | null
  est_bars?: number | null
  entry_zone: number | null
  target:  number | null   // alias target1 for backward compat
  target1: number | null
  target2: number | null
  target3: number | null
  stop_loss: number | null
  analysis: string
  timestamp_ms: number
  resolution: string
  ms_pattern?: string
  ms_confidence?: number
  regime?: 'STRONG_UP' | 'UP' | 'RANGE' | 'DOWN' | 'STRONG_DOWN' | null
  regime_label?: string | null
  regime_score?: number | null
  analysis_bid?: number | null
  trade_status?: 'HOLD' | 'CLOSE' | 'PARTIAL_TP' | null
  trade_note?: string | null
  prediction_updated?: boolean | null
  update_reason?: string | null
}

// /api/losing_trades — per-timeframe loss stats + losing trade list
export interface LosingTrade {
  id:         number
  created_at: string
  signal:     Signal
  conviction: string | null
  entry:      number
  target:     number
  stop_loss:  number
  loss_pct:   number | null
  regime:     string | null
}

export interface TimeframeLossStat {
  resolution: string
  wins:       number
  losses:     number
  pending:    number
  decided:    number
  rate:       number | null
  losers:     LosingTrade[]
}

export interface LosingStats {
  symbol:       string
  timeframes:   TimeframeLossStat[]
  total_losses: number
}
