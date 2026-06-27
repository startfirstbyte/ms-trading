import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartingLibraryWidget, EntityId } from 'charting_library'
import type { AICard } from '../types/api'

// ── Types ────────────────────────────────────────────────────────────────────

export interface ZoneLine {
  price:   number
  color:   string
  label:   string
  style:   number   // 0 solid · 1 dotted · 2 dashed
  width:   number
  tfRes:   string   // source resolution
}

const TF_LABEL: Record<string, string> = { '1':'1m', '3':'3m', '5':'5m', '15':'15m', '60':'1H' }
const DEDUP_TOL = 0.0015   // 0.15% — zones closer than this are merged

// ── Step 1: Preprocess (pure) ────────────────────────────────────────────────

/**
 * Collect, filter and deduplicate zones from all relevant TFs.
 * Current TF → all zone types.
 * Higher TFs → KEY LEVEL only (less noise).
 * Zones within DEDUP_TOL of an already-accepted zone are dropped.
 */
function preprocessZones(
  cards:      Record<string, AICard>,
  resolution: string,
): ZoneLine[] {
  const curNum     = Number(resolution)
  const accepted:  ZoneLine[] = []
  const prices:    number[]   = []

  function isDup(price: number): boolean {
    return prices.some(p => Math.abs(price - p) / p < DEDUP_TOL)
  }

  function accept(zone: ZoneLine) {
    if (isDup(zone.price)) return
    accepted.push(zone)
    prices.push(zone.price)
  }

  function makeZone(
    price: number, color: string, label: string,
    style: number, width: number, tfRes: string,
  ): ZoneLine {
    return { price, color, label, style, width, tfRes }
  }

  // ── Current TF: all zones ────────────────────────────────────────────────
  const cur = cards[resolution]
  if (cur) {
    if (cur.watch_buy  != null) accept(makeZone(cur.watch_buy,  '#26a69a', 'BUY ZONE',  0, 1, resolution))
    if (cur.watch_sell != null) accept(makeZone(cur.watch_sell, '#ef5350', 'SELL ZONE', 0, 1, resolution))
    if (cur.entry_zone != null) {
      const dup = (cur.watch_buy  != null && Math.abs(cur.entry_zone - cur.watch_buy)  < 1) ||
                  (cur.watch_sell != null && Math.abs(cur.entry_zone - cur.watch_sell) < 1)
      if (!dup) accept(makeZone(cur.entry_zone, '#e6edf3', 'ENTRY', 2, 1, resolution))
    }
    if (cur.target    != null) accept(makeZone(cur.target,    '#00e676', 'TP', 2, 1, resolution))
    if (cur.stop_loss != null) accept(makeZone(cur.stop_loss, '#ef5350', 'SL', 2, 1, resolution))
    if (cur.key_level != null) accept(makeZone(cur.key_level, '#58a6ff', 'KEY LEVEL', 1, 1, resolution))
  }

  // ── Higher TFs: KEY LEVEL only, highest first (most significant) ─────────
  const higherTFs = Object.entries(cards)
    .filter(([r]) => Number(r) > curNum)
    .sort(([a], [b]) => Number(b) - Number(a))

  for (const [tfRes, card] of higherTFs) {
    if (card.key_level == null) continue
    const tf = TF_LABEL[tfRes] ?? tfRes
    accept(makeZone(card.key_level, '#58a6ff', `${tf} KEY`, 1, 2, tfRes))
  }

  return accepted
}

// ── Step 2: Draw (side effect) ───────────────────────────────────────────────

function drawShape(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  chart: any,
  zone:  ZoneLine,
  segStart: number,
  segEnd:   number,
): EntityId | null {
  try {
    const raw = chart.createMultipointShape(
      [
        { time: segStart, price: zone.price },
        { time: segEnd,   price: zone.price },
      ],
      {
        shape: 'trend_line',
        lock: false,
        disableSelection: false,
        disableSave: true,
        disableUndo: true,
        overrides: {
          linecolor:   zone.color,
          linewidth:   zone.width,
          linestyle:   zone.style,
          extendRight: false,
          extendLeft:  false,
          showLabel:   true,
          text:        zone.label,
          textcolor:   zone.color,
          fontsize:    11,
          bold:        false,
          horzLabelsAlign: zone.label.includes('KEY') ? 'right' : 'left',
        },
      },
    )
    return raw != null ? (raw as EntityId) : null
  } catch {
    return null
  }
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export function useAiZoneLines(
  widgetRef:    RefObject<IChartingLibraryWidget | null>,
  chartReadyRef: RefObject<boolean>,
  cards:        Record<string, AICard>,
  resolution:   string,
) {
  const resolutionRef = useRef(resolution)
  resolutionRef.current = resolution

  const entityMapRef = useRef<Map<string, EntityId>>(new Map())
  const drawnResRef  = useRef<string>('')

  // ── Step 1: Preprocess synchronously whenever cards or resolution changes ──
  const zonesToDraw = useMemo(
    () => preprocessZones(cards, resolution),
    [cards, resolution],
  )

  // Fingerprint: sorted price+label string — stable across re-renders with same data
  const zoneFingerprint = useMemo(
    () => zonesToDraw.map(z => `${z.label}:${z.price.toFixed(2)}`).sort().join('|'),
    [zonesToDraw],
  )

  // ── Helpers ───────────────────────────────────────────────────────────────

  const getChart = useCallback(() => {
    if (!widgetRef.current) return null
    try { return widgetRef.current.chart() } catch { return null }
  }, [widgetRef])

  const removeAll = useCallback(() => {
    const chart = getChart()
    if (chart) {
      for (const id of entityMapRef.current.values()) {
        try { chart.removeEntity(id) } catch {}
      }
    }
    entityMapRef.current.clear()
  }, [getChart])

  // ── Step 2: Draw from preprocessed list ───────────────────────────────────

  const applyZones = useCallback((zones: ZoneLine[]) => {
    removeAll()
    const chart = getChart()
    if (!chart) return

    const res       = resolutionRef.current
    const candleSec = Number(res) * 60
    const now       = Math.floor(Date.now() / 1000)
    const segStart  = now                      // start at current candle
    const segEnd    = now + 20 * candleSec     // extend 20 candles right — no need to redraw as time passes

    for (const zone of zones) {
      const id = drawShape(chart, zone, segStart, segEnd)
      if (id != null) entityMapRef.current.set(`${zone.tfRes}:${zone.price.toFixed(2)}`, id)
    }

    drawnResRef.current = res
  }, [removeAll, getChart])

  // ── Public API ────────────────────────────────────────────────────────────

  // Keep latest zones accessible for redrawZones without closing over stale value
  const zonesToDrawRef = useRef(zonesToDraw)
  zonesToDrawRef.current = zonesToDraw

  /** Called by Chart.tsx after setSymbol — full redraw with latest zones */
  const redrawZones = useCallback(() => {
    applyZones(zonesToDrawRef.current)
  }, [applyZones])

  /** Called by Chart.tsx before setSymbol — wipe entities from old chart state */
  const clearZones = useCallback(() => {
    removeAll()
    drawnResRef.current = ''
  }, [removeAll])

  // ── Auto-apply only when zone PRICES actually changed (not just re-render) ──
  useEffect(() => {
    if (!chartReadyRef.current) return
    if (drawnResRef.current !== resolution) return   // TF switch — handled by Chart.tsx
    applyZones(zonesToDraw)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneFingerprint])   // fingerprint = stable unless prices genuinely changed

  return { redrawZones, clearZones }
}
