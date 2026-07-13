import React, { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartingLibraryWidget } from 'charting_library'
import type { Resolution } from '../../types/api'
import { createDatafeed } from '../../lib/datafeed'
import { api } from '../../lib/api'
import { useMarketStructure } from '../../hooks/useMarketStructure'
import { useChartState } from '../../hooks/useChartState'
import styles from './Chart.module.css'

interface Props {
  symbol:     string
  resolution: Resolution
  recalcRef?: RefObject<(() => void) | null>
}

type TVPoint  = { price?: unknown }
type TVSource = { points?: TVPoint[] }
type TVPane   = { sources?: TVSource[] }
type TVChart  = { panes?: TVPane[] }
type TVState  = { charts?: TVChart[] }

function sanitizeChartState(raw: TVState): TVState {
  if (!raw?.charts) return raw
  return {
    ...raw,
    charts: raw.charts.map(chart => ({
      ...chart,
      panes: chart.panes?.map(pane => ({
        ...pane,
        sources: pane.sources?.filter(src => {
          const pts = src?.points
          if (!pts) return true                        // no points field → keep (e.g. MainSeries)
          if (pts.length === 0) return false           // empty points → TV crashes on restore
          return pts.every(pt => pt?.price != null && isFinite(pt.price as number))
        }),
      })),
    })),
  }
}

const WIDGET_OVERRIDES: Record<string, unknown> = {
  'mainSeriesProperties.candleStyle.upColor':         '#26a69a',
  'mainSeriesProperties.candleStyle.downColor':       '#ef5350',
  'mainSeriesProperties.candleStyle.borderUpColor':   '#26a69a',
  'mainSeriesProperties.candleStyle.borderDownColor': '#ef5350',
  'mainSeriesProperties.candleStyle.wickUpColor':     '#26a69a',
  'mainSeriesProperties.candleStyle.wickDownColor':   '#ef5350',
  'paneProperties.background':                        '#131722',
  'paneProperties.backgroundType':                    'solid',
  'paneProperties.gridLinesMode':                     'both',
  'paneProperties.vertGridProperties.color':          '#1e222d',
  'paneProperties.horzGridProperties.color':          '#1e222d',
  'scalesProperties.backgroundColor':                 '#131722',
  'mainSeriesProperties.showCountdown':               true,
}

export function Chart({ symbol, resolution, recalcRef }: Props) {
  const containerRef  = useRef<HTMLDivElement>(null)
  const widgetRef     = useRef<IChartingLibraryWidget | null>(null)
  const chartReadyRef = useRef<boolean>(false)
  const datafeedRef   = useRef<ReturnType<typeof createDatafeed> | null>(null)

  const { chip: _chip, signal, redraw, clearShapes } = useMarketStructure(widgetRef, chartReadyRef, symbol, resolution)
  const { attach: attachChartState, saveNow }  = useChartState(widgetRef, chartReadyRef, symbol)
  const attachRef     = useRef(attachChartState)
  const saveRef       = useRef(saveNow)
  attachRef.current      = attachChartState
  saveRef.current        = saveNow

  const redrawRef = useRef(redraw)
  const clearRef  = useRef(clearShapes)
  redrawRef.current = redraw
  clearRef.current  = clearShapes

  if (recalcRef) recalcRef.current = () => { redrawRef.current() }

  // ── Init widget once on mount ─────────────────────────────────────────────

  useEffect(() => {
    const container = containerRef.current
    if (!container || !window.TradingView) return
    let cancelled = false

    const datafeed = createDatafeed()
    datafeedRef.current = datafeed

    // Fetch the user's saved drawings FIRST, then build the widget with
    // `saved_data` so the restore is atomic at construction. Previously this
    // ran as an async `widget.load()` AFTER chart-ready, which re-applied the
    // whole layout a beat later and stripped the selection from a shape the
    // user had just drawn — making a fresh long position impossible to drag.
    const buildWidget = (savedData?: object) => {
      if (cancelled || !container) return
      const options = {
        container,
        library_path: '/charting_library/',
        datafeed,
        symbol,
        interval:     resolution,
        locale:       'en',
        theme:        'Dark',
        autosize:     true,
        timezone:     'Etc/UTC',
        disabled_features: [
          'header_symbol_search',   // ẩn nút 🔍 symbol (đã có Toolbar riêng)
          'header_resolutions',     // ẩn nút interval/1h (đã có Toolbar riêng)
          'header_compare',
          'header_undo_redo',
          'header_saveload',
          'use_localstorage_for_settings',
          'header_account_manager',
          'trading_account_manager',
          'show_right_widgets_panel_by_default',
          'symbol_search_hot_key',
          'popup_hints',   // tắt tooltip onboarding "Press and hold to see…"
        ],
        enabled_features: [
          'hide_left_toolbar_by_default',
        ],
        overrides: WIDGET_OVERRIDES,
        ...(savedData ? { saved_data: savedData } : {}),
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any

      widgetRef.current = new window.TradingView.widget(options)
      widgetRef.current.onChartReady(() => {
        chartReadyRef.current = true
        // Scroll to realtime (latest bar) on every load so stale saved-viewport
        // doesn't hide recent bars after backend restart or weekend gap.
        try {
          widgetRef.current?.chart().executeActionById('timeScaleReset')
        } catch { /* ignore if action unavailable */ }
        // EMA overlay bị bỏ (quá nhiều đường EMA trùng nhau) — dọn luôn các study
        // EMA cũ đã lỡ lưu trong saved_data để không còn tồn đọng trên chart.
        try {
          const chart = widgetRef.current?.chart()
          for (const s of chart?.getAllStudies() ?? []) {
            if (s.name === 'Moving Average Exponential') chart!.removeEntity(s.id)
          }
        } catch { /* ignore if study unavailable */ }
        redrawRef.current()
        attachRef.current()        // subscribe to auto-save (drawings already restored)
      })
    }

    api.chartStateLoad(symbol)
      .then(saved => {
        let parsed: object | undefined
        if (saved) {
          try {
            const raw = typeof saved === 'string' ? JSON.parse(saved) : saved
            parsed = sanitizeChartState(raw as TVState)
          } catch { parsed = undefined }
        }
        buildWidget(parsed)
      })
      .catch(() => buildWidget(undefined))

    return () => {
      cancelled = true
      chartReadyRef.current = false
      datafeed.closeAll()
      try { widgetRef.current?.remove() } catch { /* ignore */ }
      widgetRef.current   = null
      datafeedRef.current = null
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Switch symbol/resolution: clear immediately, redraw after chart loads ──

  useEffect(() => {
    const widget = widgetRef.current
    if (!widget || !chartReadyRef.current) return
    saveRef.current()   // save drawings of old symbol before switching
    clearRef.current()
    widget.setSymbol(symbol, resolution, () => {
      redrawRef.current()
    })
  }, [symbol, resolution])

  const sigColor = signal.signal === 'BUY' ? '#26a69a' : signal.signal === 'SELL' ? '#ef5350' : '#787b86'
  const sigStyle: React.CSSProperties = {
    position: 'absolute', top: 8, left: '50%', transform: 'translateX(-50%)',
    background: sigColor, color: '#fff', fontWeight: 700, fontSize: 13,
    padding: '3px 14px', borderRadius: 4, pointerEvents: 'none',
    display: signal.signal === 'WAIT' ? 'none' : 'block',
    zIndex: 10, letterSpacing: 1,
  }

  return (
    <div ref={containerRef} className={styles.chart} style={{ position: 'relative' }}>
      <div style={sigStyle}>{signal.signal}</div>
    </div>
  )
}
