import { useCallback, useEffect, useRef, useState } from 'react'
import type { AICard } from '../types/api'
import { AI_RESOLUTIONS } from '../types/domain'
import { api, WS_BASE } from '../lib/api'

function fmtAge(ts: number): string {
  if (!ts) return ''
  const min = Math.round((Date.now() - ts) / 60_000)
  if (min < 1) return 'vừa xong'
  return `${min} phút trước`
}

export function fmtPrice(p: number | null | undefined): string {
  if (p == null) return '—'
  if (p > 10000) return p.toFixed(0)
  if (p > 100)   return p.toFixed(2)
  if (p > 1)     return p.toFixed(3)
  return p.toFixed(5)
}

export function fmtAgeOf(ts: number | undefined): string {
  return ts ? fmtAge(ts) : ''
}

export function useAiWs(symbol: string, _resolution: string) {
  const [cards, setCards] = useState<Record<string, AICard>>({})
  const [status, setStatus] = useState('Nhấn Analyze để phân tích')
  const [analyzing, setAnalyzing] = useState(false)

  const wsRef        = useRef<WebSocket | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const symbolRef    = useRef(symbol)
  const analyzingRef = useRef(false)

  useEffect(() => { symbolRef.current = symbol }, [symbol])

  // ── Age ticker (30s) ──────────────────────────────────────────────────────

  const [ageTick, setAgeTick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setAgeTick(n => n + 1), 30_000)
    return () => clearInterval(t)
  }, [])

  const cardAges = Object.fromEntries(
    Object.entries(cards).map(([k, v]) => [k, fmtAge(v.timestamp_ms)])
  )
  void ageTick

  // ── Update card ───────────────────────────────────────────────────────────

  const updateCard = useCallback((data: Record<string, unknown>) => {
    const res = data.resolution as string
    if (!res) return
    setCards(prev => ({
      ...prev,
      [res]: {
        signal:      (data.signal as AICard['signal'])        ?? 'WAIT',
        conviction:  (data.conviction as AICard['conviction']) ?? 'LOW',
        win_pct:     data.win_pct     as number | null | undefined,
        trigger:     data.trigger     as string | undefined,
        watch_buy:   data.watch_buy   as number | null | undefined,
        watch_sell:  data.watch_sell  as number | null | undefined,
        key_level:   data.key_level   as number | null | undefined,
        est_bars:    data.est_bars    as number | null | undefined,
        entry_zone:  (data.entry_zone as number)  ?? null,
        target:      (data.target1 as number)     ?? (data.target as number) ?? null,
        target1:     (data.target1 as number)     ?? null,
        target2:     (data.target2 as number)     ?? null,
        target3:     (data.target3 as number)     ?? null,
        stop_loss:   (data.stop_loss as number)   ?? null,
        analysis:    (data.analysis as string)    ?? '—',
        timestamp_ms:(data.timestamp_ms as number) ?? 0,
        resolution:  res,
        ms_pattern:    data.ms_pattern    as string | undefined,
        ms_confidence: data.ms_confidence as number | undefined,
        regime:        data.regime        as AICard['regime'],
        regime_label:  data.regime_label  as string | null | undefined,
        regime_score:  data.regime_score  as number | null | undefined,
        analysis_bid:       data.analysis_bid       as number | null | undefined,
        trade_status:       data.trade_status        as AICard['trade_status'],
        trade_note:         data.trade_note          as string | null | undefined,
        prediction_updated: data.prediction_updated  as boolean | null | undefined,
        update_reason:      data.update_reason       as string | null | undefined,
      },
    }))
    const ep = data.ms_pattern as string | undefined
    const ec = data.ms_confidence as number | undefined
    if (ep && ep !== 'none') {
      setStatus(`${symbolRef.current} — ${ep} (${Math.round((ec ?? 0) * 100)}%)`)
    } else {
      setStatus(symbolRef.current)
    }
  }, [])

  // ── HTTP fallback ─────────────────────────────────────────────────────────

  const fetchCache = useCallback(async () => {
    await Promise.all(
      AI_RESOLUTIONS.map(async res => {
        try {
          const data = await api.aiAnalysisCache(symbolRef.current, res)
          updateCard({ ...data, resolution: res })
        } catch { /* ignore */ }
      })
    )
  }, [updateCard])

  // ── WebSocket ─────────────────────────────────────────────────────────────

  const openWs = useCallback(() => {
    const ws = new WebSocket(`${WS_BASE}/ai/${symbolRef.current}`)
    wsRef.current = ws

    ws.onopen = () => {
      if (pollTimerRef.current) { clearInterval(pollTimerRef.current); pollTimerRef.current = null }
    }

    ws.onmessage = ({ data }: MessageEvent) => {
      try {
        const msg = JSON.parse(data as string) as Record<string, unknown>
        if (msg.resolution) updateCard(msg)
      } catch { /* ignore */ }
    }

    ws.onclose = () => {
      if (wsRef.current !== ws) return
      wsRef.current = null
      pollTimerRef.current = setInterval(() => {
        void fetchCache()
        openWs()
      }, 30_000)
    }

    ws.onerror = () => ws.close()
  }, [updateCard, fetchCache])

  useEffect(() => {
    const prevWs = wsRef.current
    if (prevWs) {
      wsRef.current = null
      if (prevWs.readyState !== WebSocket.CONNECTING) prevWs.close(1000)
      else prevWs.onopen = () => prevWs.close(1000)
    }
    if (pollTimerRef.current) { clearInterval(pollTimerRef.current); pollTimerRef.current = null }
    setCards({})
    setStatus('Nhấn Analyze để phân tích')
    openWs()

    return () => {
      const ws = wsRef.current
      if (ws) {
        wsRef.current = null
        if (ws.readyState !== WebSocket.CONNECTING) ws.close(1000)
        else ws.onopen = () => ws.close(1000)
      }
      if (pollTimerRef.current) { clearInterval(pollTimerRef.current); pollTimerRef.current = null }
    }
  }, [symbol, openWs])

  // ── Trigger analysis ──────────────────────────────────────────────────────

  const trigger = useCallback(async (force = false, resolution?: string) => {
    if (analyzingRef.current) return
    analyzingRef.current = true
    setAnalyzing(true)
    setStatus(force
      ? `Force re-analyzing ${symbolRef.current}…`
      : `Analyzing ${symbolRef.current} — checking cache…`)

    try {
      await api.aiAnalyze(symbolRef.current, force, resolution)
    } catch {
      setStatus('Network error — check server')
    } finally {
      analyzingRef.current = false
      setAnalyzing(false)
    }
  }, [])

  return {
    cards,
    cardAges,
    status,
    trigger,
    analyzing,
  }
}
