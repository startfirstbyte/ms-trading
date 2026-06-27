import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { AiPrediction } from '../types/api'

// Fetch the last `limit` predictions for one (symbol, resolution) on demand.
// Pass resolution=null to stay idle (e.g. history panel closed).
export function useAiHistory(symbol: string, resolution: string | null, limit = 10) {
  const [data, setData]       = useState<AiPrediction[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(false)

  useEffect(() => {
    if (!resolution) { setData([]); return }
    let cancelled = false
    setLoading(true)
    setError(false)
    api.aiHistory(symbol, resolution, limit)
      .then(rows => { if (!cancelled) setData(rows) })
      .catch(() => { if (!cancelled) setError(true) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [symbol, resolution, limit])

  return { data, loading, error }
}
