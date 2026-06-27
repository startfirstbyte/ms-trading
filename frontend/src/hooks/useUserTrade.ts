import { useCallback, useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartingLibraryWidget } from 'charting_library'
import { api, type UserPositionItem } from '../lib/api'

// Debounce so a re-sync (which may trigger a forced AI re-analysis) fires only
// after the user finishes (lock / move / delete), not on every intermediate event.
const SYNC_DEBOUNCE_MS = 1200

// Toggle to log raw TV shape properties while tuning SL/TP / lock parsing.
const DEBUG = false

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

// TV point time is UNIX seconds; normalise to ms.
function toMs(t: unknown): number | null {
  const n = num(t)
  if (n == null) return null
  return n < 1e12 ? Math.round(n * 1000) : Math.round(n)
}

// The Long/Short Position tool stores stop/profit as `stopLevel`/`profitLevel`,
// measured in TICKS (not price). Convert to a price offset via minTick
// (= minmov/pricescale, e.g. 0.01 for BTCUSD). Then validate the result sits on
// the correct side of entry — otherwise drop it (null).
function readLevels(
  side:    'BUY' | 'SELL',
  entry:   number,
  minTick: number,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  props:   Record<string, any>,
): { stop: number | null; target: number | null } {
  // Absolute-price props (some TV builds) take precedence if present.
  let stop   = num(props.stopPrice)   ?? num(props.stopLevelPrice)
  let target = num(props.profitPrice) ?? num(props.profitLevelPrice)

  const stopOff   = num(props.stopLevel)
  const profitOff = num(props.profitLevel)
  if (stop   == null && stopOff   != null) {
    const d = stopOff * minTick
    stop = side === 'BUY' ? entry - d : entry + d
  }
  if (target == null && profitOff != null) {
    const d = profitOff * minTick
    target = side === 'BUY' ? entry + d : entry - d
  }

  if (stop   != null && (side === 'BUY' ? stop   >= entry : stop   <= entry)) stop   = null
  if (target != null && (side === 'BUY' ? target <= entry : target >= entry)) target = null

  // Round to the instrument's tick precision (drops float artefacts like ...1499994)
  const dec = minTick > 0 ? Math.max(0, Math.round(-Math.log10(minTick))) : 2
  const round = (x: number | null) => (x == null ? null : Number(x.toFixed(dec)))
  return { stop: round(stop), target: round(target) }
}

export function useUserTrade(
  widgetRef:     RefObject<IChartingLibraryWidget | null>,
  chartReadyRef: RefObject<boolean>,
  symbol:        string,
  resolution:    string,
) {
  const lastSigRef = useRef<string | null>(null)   // signature of last synced set
  const timerRef   = useRef<ReturnType<typeof setTimeout> | null>(null)
  const symbolRef  = useRef(symbol);     symbolRef.current     = symbol
  const resRef     = useRef(resolution); resRef.current        = resolution
  const minTickRef = useRef<number>(0.01)   // price per tick, from /api/resolve

  // Fetch the symbol's tick size so stopLevel/profitLevel (in ticks) → price.
  useEffect(() => {
    let cancelled = false
    api.resolve(symbol).then(info => {
      if (cancelled) return
      const ps = num((info as Record<string, unknown>).pricescale) ?? 100
      const mm = num((info as Record<string, unknown>).minmov)     ?? 1
      if (ps > 0) minTickRef.current = mm / ps
    }).catch(() => {})
    return () => { cancelled = true }
  }, [symbol])

  const sync = useCallback(() => {
    const widget = widgetRef.current
    if (!widget || !chartReadyRef.current) return
    let chart: ReturnType<IChartingLibraryWidget['chart']>
    try { chart = widget.chart() } catch { return }

    let shapes: { id: string; name: string }[]
    try { shapes = chart.getAllShapes() as { id: string; name: string }[] } catch (e) {
      if (DEBUG) console.warn('[useUserTrade] getAllShapes threw', e)
      return
    }

    const sym = symbolRef.current
    const res = resRef.current

    if (DEBUG) console.log('[useUserTrade] sync — all shape names:', shapes.map(s => s.name))

    const positions: UserPositionItem[] = []
    for (const sh of shapes) {
      if (sh.name !== 'long_position' && sh.name !== 'short_position') continue
      let info: ReturnType<ReturnType<IChartingLibraryWidget['chart']>['getShapeById']>
      try { info = chart.getShapeById(sh.id as never) } catch { continue }

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let props: Record<string, any> = {}
      try { props = info.getProperties() ?? {} } catch { /* ignore */ }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let pts: any[] = []
      try { pts = (info.getPoints?.() as unknown[] ?? []) as never[] } catch { /* ignore */ }
      if (DEBUG) console.log(
        '[useUserTrade] position', sh.name,
        'lock=', props.lock,
        'pointsLen=', pts.length,
        'point0=', pts[0],
        'propKeys=', Object.keys(props).join(','),
      )

      // Only LOCKED positions count as real entered orders.
      // TradingView stores the lock state as `frozen` (not `lock`).
      const isLocked = props.frozen === true || props.lock === true
      if (!isLocked) { if (DEBUG) console.log('[useUserTrade]  → SKIP (chưa khóa, frozen=', props.frozen, ')'); continue }

      const entry  = num(pts[0]?.price)
      const tEntry = toMs(pts[0]?.time)
      if (entry == null || tEntry == null) { if (DEBUG) console.log('[useUserTrade]  → SKIP (entry/time null) entry=', entry, 'time=', tEntry); continue }

      const side: 'BUY' | 'SELL' = sh.name === 'long_position' ? 'BUY' : 'SELL'
      const { stop, target } = readLevels(side, entry, minTickRef.current, props)
      if (DEBUG) console.log('[useUserTrade]  → ACCEPT', { shape_id: sh.id, side, entry, stop, target, entry_time_ms: tEntry })
      positions.push({ shape_id: String(sh.id), side, entry, stop, target, entry_time_ms: tEntry })
    }

    const sig = JSON.stringify(positions)
    if (sig === lastSigRef.current) { if (DEBUG) console.log('[useUserTrade] sig không đổi, bỏ POST. positions=', positions.length); return }
    lastSigRef.current = sig
    if (DEBUG) console.log('[useUserTrade] POST /api/user_position — positions=', positions)
    void api.userPositionSync(sym, res, positions).catch(e => { if (DEBUG) console.warn('[useUserTrade] POST failed', e) })
  }, [widgetRef, chartReadyRef])

  const scheduleSync = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(sync, SYNC_DEBOUNCE_MS)
  }, [sync])

  // Called from onChartReady — subscribe to drawing changes + initial scan.
  // `drawing_event` fires on create/move/remove/properties_changed — crucially
  // including LOCK (a property change), which `onAutoSaveNeeded` does NOT emit.
  const attachUserTrade = useCallback(() => {
    const widget = widgetRef.current
    if (!widget) { if (DEBUG) console.warn('[useUserTrade] attach: no widget'); return }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const onDraw = (id: unknown, type: unknown) => {
      if (DEBUG) console.log('[useUserTrade] drawing_event', type, id)
      scheduleSync()
    }
    try { widget.subscribe('drawing_event', onDraw as never) } catch (e) { if (DEBUG) console.warn('[useUserTrade] drawing_event subscribe failed', e) }
    try { widget.subscribe('onAutoSaveNeeded', scheduleSync) } catch { /* fallback */ }
    if (DEBUG) console.log('[useUserTrade] attached, subscriptions set')
    sync()
  }, [widgetRef, scheduleSync, sync])

  // Symbol/resolution switch changes which (symbol,res) positions map to.
  useEffect(() => {
    lastSigRef.current = null
    scheduleSync()
  }, [symbol, resolution, scheduleSync])

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  return { attachUserTrade }
}
