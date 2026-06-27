import type { Resolution } from './api'

export interface SymbolDef {
  name: string    // e.g. 'XAUUSD'
  label: string   // e.g. 'XAU/USD'
}

export interface TimeframeDef {
  label: string         // e.g. '1m'
  resolution: Resolution
}

export const SYMBOLS: SymbolDef[] = [
  { name: 'XAUUSD', label: 'XAU/USD' },
  { name: 'BTCUSD', label: 'BTC/USD' },
  { name: 'USOIL',  label: 'OIL' },
]

export const TIMEFRAMES: TimeframeDef[] = [
  { label: '1m',  resolution: '1' },
  { label: '3m',  resolution: '3' },
  { label: '5m',  resolution: '5' },
  { label: '15m', resolution: '15' },
  { label: '1H',  resolution: '60' },
]

export const VALID_SYMBOLS: string[]     = SYMBOLS.map(s => s.name)
export const VALID_RESOLUTIONS: string[] = TIMEFRAMES.map(t => t.resolution)

// MS scan window (lookback bars) per timeframe
export const MS_LOOKBACK: Record<string, number> = {
  '1': 200, '3': 100, '5': 100, '15': 80, '60': 300,
}

export const MS_CACHE_KEY = 'msCache'
export const MS_CACHE_TTL = 5 * 60_000   // 5 minutes

export const AI_RESOLUTIONS: Resolution[] = ['1', '3', '5', '15', '60']

export const AI_TF_LABEL: Record<string, string> = {
  '1': '1m', '3': '3m', '5': '5m', '15': '15m', '60': '1H',
}

