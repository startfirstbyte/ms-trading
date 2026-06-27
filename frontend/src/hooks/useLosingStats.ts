import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { LosingStats } from '../types/api'

// Fetch per-timeframe losing-trade stats for a symbol when the panel is open.
export function useLosingStats(symbol: string, open: boolean) {
  const [data, setData]       = useState<LosingStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setError(false)
    api.losingTrades(symbol)
      .then(d => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setError(true) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [symbol, open])

  return { data, loading, error }
}
