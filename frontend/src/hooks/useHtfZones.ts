import { useCallback, useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartingLibraryWidget, EntityId } from 'charting_library'
import type { AICard } from '../types/api'
import { api } from '../lib/api'
import { fmtPrice } from './useAiWs'

// Draws the 1H timeframe's zones (BUY/SELL/KEY) onto the chart as short
// horizontal lines, from each zone's confirmation point up to the current
// candle. The zone definition is cached in localStorage so it survives reloads
// and WebSocket timing, and is redrawn on EVERY timeframe (not just the one it
// was first drawn on). When the 1H zone changes, old lines are cleared + redrawn.

type Pt = { time: number; price: number }
type Kind = 'buy' | 'sell' | 'key'
type Wave = { time: number; price: number; type?: string; label?: string }
type Zones = { buy: number | null; sell: number | null; key: number | null }

const COLOR: Record<Kind, string> = { buy: '#3fb950', sell: '#f85149', key: '#58a6ff' }
const MAX_BACK_BARS = 30   // cap line length to this many bars of the viewed TF
const LS_KEY = (sym: string) => `htfZones:${sym}`

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function zoneStartSec(price: number, kind: Kind, waves: Wave[],
                      channelStartMs: number | null, nowSec: number): number {
  const tol = price * 0.004
  let best: Wave | null = null
  let bestDiff = Infinity
  for (const w of waves) {
    if (kind === 'buy'  && w.type && w.type !== 'low')  continue
    if (kind === 'sell' && w.type && w.type !== 'high') continue
    const d = Math.abs(w.price - price)
    if (d <= tol && d < bestDiff) { bestDiff = d; best = w }
  }
  if (best) return Math.floor(best.time / 1000)
  if (channelStartMs) return Math.floor(channelStartMs / 1000)
  return nowSec - 12 * 3600
}

export function useHtfZones(
  widgetRef:     RefObject<IChartingLibraryWidget | null>,
  chartReadyRef: RefObject<boolean>,
  symbol:        string,
  resolution:    string,
  card1H:        AICard | undefined,
) {
  const idsRef    = useRef<Promise<EntityId | null>[]>([])
  const zonesRef  = useRef<Zones>({ buy: null, sell: null, key: null })
  const symbolRef = useRef(symbol);     symbolRef.current = symbol
  const resRef    = useRef(resolution); resRef.current    = resolution

  const clear = useCallback(() => {
    const widget = widgetRef.current
    const pending = idsRef.current
    idsRef.current = []
    if (!widget || !pending.length) return
    let chart: ReturnType<IChartingLibraryWidget['chart']>
    try { chart = widget.chart() } catch { return }
    Promise.all(pending).then(ids => {
      for (const id of ids) if (id != null) { try { chart.removeEntity(id) } catch { /* gone */ } }
    }).catch(() => {})
  }, [widgetRef])

  // Always clears + redraws from zonesRef (no dedup) so it works after a
  // timeframe switch that wiped the previous shapes.
  const draw = useCallback(async () => {
    const widget = widgetRef.current
    if (!widget || !chartReadyRef.current) return
    clear()

    const z = zonesRef.current
    const specs: { price: number; kind: Kind; label: string }[] = []
    if (z.buy  != null) specs.push({ price: z.buy,  kind: 'buy',  label: `1H BUY ${fmtPrice(z.buy)}` })
    if (z.sell != null) specs.push({ price: z.sell, kind: 'sell', label: `1H SELL ${fmtPrice(z.sell)}` })
    if (z.key  != null) specs.push({ price: z.key,  kind: 'key',  label: `1H KEY ${fmtPrice(z.key)}` })
    if (!specs.length) return

    const sym = symbolRef.current
    let waves: Wave[] = []
    let channelStartMs: number | null = null
    try {
      const ms = await api.ms(sym, '60')
      waves = (ms.waves as Wave[]) ?? []
      channelStartMs = num((ms.channel as Record<string, unknown> | undefined)?.time_start)
    } catch { /* fall back below */ }

    if (sym !== symbolRef.current || !chartReadyRef.current) return
    let chart: ReturnType<IChartingLibraryWidget['chart']>
    try { chart = widget.chart() } catch { return }

    const nowSec   = Math.floor(Date.now() / 1000)
    const resSec   = (Number(resRef.current) || 5) * 60
    const earliest = nowSec - MAX_BACK_BARS * resSec
    const ids: Promise<EntityId | null>[] = []
    for (const s of specs) {
      const startSec = Math.max(zoneStartSec(s.price, s.kind, waves, channelStartMs, nowSec), earliest)
      const pts: Pt[] = [{ time: startSec, price: s.price }, { time: nowSec, price: s.price }]
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const raw = (chart as any).createMultipointShape(pts, {
          shape: 'trend_line',
          lock: true, disableSelection: true, disableSave: true, disableUndo: true,
          overrides: {
            linecolor:  COLOR[s.kind],
            linewidth:  2,
            linestyle:  s.kind === 'key' ? 2 : 0,
            showLabel:  true,
            text:       s.label,
            textcolor:  COLOR[s.kind],
            fontsize:   11,
            bold:       true,
            horzLabelsAlign: s.kind === 'key' ? 'right' : 'left',
            vertLabelsAlign: 'top',
            extendLeft:  false,
            extendRight: false,
          },
        })
        ids.push(Promise.resolve(raw as unknown as EntityId | null))
      } catch { /* shape unavailable */ }
    }
    idsRef.current = ids
  }, [widgetRef, chartReadyRef, clear])

  const drawRef = useRef(draw)
  drawRef.current = draw

  // Sync zonesRef from the live 1H card; persist to localStorage. If no card yet
  // (fresh load / WS not connected), fall back to the last-known stored zones.
  useEffect(() => {
    const buy = num(card1H?.watch_buy), sell = num(card1H?.watch_sell), key = num(card1H?.key_level)
    if (buy != null || sell != null || key != null) {
      const z: Zones = { buy, sell, key }
      zonesRef.current = z
      try { localStorage.setItem(LS_KEY(symbol), JSON.stringify(z)) } catch { /* ignore */ }
    } else {
      try {
        const raw = localStorage.getItem(LS_KEY(symbol))
        zonesRef.current = raw ? (JSON.parse(raw) as Zones) : { buy: null, sell: null, key: null }
      } catch { zonesRef.current = { buy: null, sell: null, key: null } }
    }
    void drawRef.current()
  }, [symbol, card1H?.watch_buy, card1H?.watch_sell, card1H?.key_level])

  // Called on chart-ready and after a symbol/timeframe switch settles.
  const redrawHtfZones = useCallback(() => { void drawRef.current() }, [])

  useEffect(() => () => clear(), [clear])

  return { attachHtfZones: redrawHtfZones, redrawHtfZones }
}
