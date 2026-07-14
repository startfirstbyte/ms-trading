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
  r2?: number          // độ tin cậy thống kê của slope (Theil-Sen fit)
}

// Channel có vòng đời, lưu DB (/api/channels)
export interface PriceChannel {
  id: number
  status: 'editing' | 'confirmed' | 'committed'
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

export interface MSZone {
  lo:       number
  hi:       number
  mid:      number
  strength: number   // số pivot/biên kênh gom vào vùng — càng cao càng "mạnh"
  recency:  number   // số bar từ pivot gần nhất trong vùng tới hiện tại
}

export interface MSSrZones {
  supports:    MSZone[]   // gần giá hiện tại nhất trước
  resistances: MSZone[]
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
  sr_zones?: MSSrZones
}
