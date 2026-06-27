import { useCallback, useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartingLibraryWidget, EntityId } from 'charting_library'
import type { AICard } from '../types/api'

interface LevelSpec {
  price:   number
  color:   string
  label:   string
  style:   number   // 0 solid · 1 dotted · 2 dashed
  width:   number
  align:   'left' | 'right'   // label horizontal align
}

function buildLevels(c: AICard): LevelSpec[] {
  const levels: LevelSpec[] = []

  if (c.entry_zone != null) levels.push({ price: c.entry_zone, color: '#ffffff', label: 'ENTRY', style: 2, width: 1, align: 'left' })
  if (c.target1    != null) levels.push({ price: c.target1,    color: '#00e676', label: 'TP1',   style: 2, width: 1, align: 'left' })
  if (c.target2    != null) levels.push({ price: c.target2,    color: '#69f0ae', label: 'TP2',   style: 1, width: 1, align: 'left' })
  if (c.target3    != null) levels.push({ price: c.target3,    color: '#b9f6ca', label: 'TP3',   style: 1, width: 1, align: 'left' })
  if (c.stop_loss  != null) levels.push({ price: c.stop_loss,  color: '#ef5350', label: 'SL',    style: 2, width: 1, align: 'left' })

  return levels
}

export function useAiLines(
  widgetRef:    RefObject<IChartingLibraryWidget | null>,
  chartReadyRef: RefObject<boolean>,
  card:         AICard | null | undefined,
  resolution:   string = '1',
) {
  const shapeIdsRef = useRef<EntityId[]>([])
  const cardRef     = useRef(card)
  cardRef.current   = card

  const clearLines = useCallback(() => {
    const widget = widgetRef.current
    if (!widget) { shapeIdsRef.current = []; return }
    let chart: ReturnType<IChartingLibraryWidget['chart']>
    try { chart = widget.chart() } catch { shapeIdsRef.current = []; return }
    for (const id of shapeIdsRef.current) {
      try { chart.removeEntity(id) } catch { /* already gone */ }
    }
    shapeIdsRef.current = []
  }, [widgetRef])

  const drawLines = useCallback(() => {
    if (!chartReadyRef.current || !widgetRef.current) return
    const c = cardRef.current
    if (!c) return

    let chart: ReturnType<IChartingLibraryWidget['chart']>
    try { chart = widgetRef.current.chart() } catch { return }

    const now        = Math.floor(Date.now() / 1000)
    const candleSec  = Number(resolution) * 60   // seconds per candle
    const start      = now - 2 * candleSec        // 2 candles left of now
    const end        = now + 3 * candleSec        // 3 candles right of now
    const ids: EntityId[] = []

    for (const lvl of buildLevels(c)) {
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const raw = (chart as any).createMultipointShape(
          [
            { time: start, price: lvl.price },
            { time: end,   price: lvl.price },
          ],
          {
            shape: 'trend_line',
            lock: false,
            disableSelection: false,
            disableSave: true,
            disableUndo: true,
            overrides: {
              linecolor:   lvl.color,
              linewidth:   lvl.width,
              linestyle:   lvl.style,
              extendRight: false,
              extendLeft:  false,
              showLabel:   true,
              text:        lvl.label,
              textcolor:   lvl.color,
              fontsize:    11,
              bold:        false,
              horzLabelsAlign: lvl.align,
            },
          },
        )
        if (raw != null) ids.push(raw as EntityId)
      } catch { /* shape API unavailable */ }
    }

    shapeIdsRef.current = ids
  }, [widgetRef, chartReadyRef])

  const redraw = useCallback(() => {
    clearLines()
    drawLines()
  }, [clearLines, drawLines])

  useEffect(() => {
    if (!chartReadyRef.current) return
    redraw()
  }, [card, redraw, chartReadyRef])

  return { redraw, clearLines }
}
