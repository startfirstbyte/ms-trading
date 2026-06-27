import { useCallback, useEffect, useState } from 'react'
import { VALID_RESOLUTIONS, VALID_SYMBOLS } from '../types/domain'
import type { Resolution } from '../types/api'

function pick<T extends string>(value: string | null, valid: string[], fallback: T): T {
  return (value != null && valid.includes(value) ? value : fallback) as T
}

function readState(): { symbol: string; resolution: Resolution } {
  const p = new URLSearchParams(location.search)
  return {
    symbol: pick(
      p.get('symbol'),
      VALID_SYMBOLS,
      pick(localStorage.getItem('symbol'), VALID_SYMBOLS, 'XAUUSD')
    ),
    resolution: pick<Resolution>(
      p.get('resolution'),
      VALID_RESOLUTIONS,
      pick<Resolution>(
        localStorage.getItem('resolution'),
        VALID_RESOLUTIONS,
        '60'
      )
    ),
  }
}

function syncUrl(symbol: string, resolution: string) {
  const p = new URLSearchParams(location.search)
  p.set('symbol', symbol)
  p.set('resolution', resolution)
  history.replaceState(null, '', `${location.pathname}?${p.toString()}`)
}

export function useUrlState() {
  const initial = readState()
  const [symbol, setSymbolRaw] = useState(initial.symbol)
  const [resolution, setResolutionRaw] = useState<Resolution>(initial.resolution)

  const setSymbol = useCallback((s: string) => {
    if (!VALID_SYMBOLS.includes(s)) return
    setSymbolRaw(s)
    localStorage.setItem('symbol', s)
    syncUrl(s, resolution)
  }, [resolution])

  const setResolution = useCallback((r: Resolution) => {
    if (!VALID_RESOLUTIONS.includes(r)) return
    setResolutionRaw(r)
    localStorage.setItem('resolution', r)
    syncUrl(symbol, r)
  }, [symbol])

  // Sync state when user navigates with browser back/forward
  useEffect(() => {
    const onPopState = () => {
      const { symbol: s, resolution: r } = readState()
      setSymbolRaw(s)
      setResolutionRaw(r)
      localStorage.setItem('symbol', s)
      localStorage.setItem('resolution', r)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  // Sync URL on mount (in case localStorage state differs from URL)
  useEffect(() => {
    syncUrl(symbol, resolution)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return { symbol, resolution, setSymbol, setResolution }
}
