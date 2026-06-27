import { useEffect, useState } from 'react'
import { api, type Winrate } from '../lib/api'
import { AI_RESOLUTIONS } from '../types/domain'

// Recent BUY/SELL win-rate per timeframe (server caches ~10 min). Refetched on
// symbol change and whenever `tick` changes (e.g. after a new analysis).
export function useWinrates(symbol: string, tick = 0) {
  const [data, setData] = useState<Record<string, Winrate>>({})

  useEffect(() => {
    let cancelled = false
    Promise.all(
      AI_RESOLUTIONS.map(res =>
        api.winrate(symbol, res)
          .then(w => [res, w] as const)
          .catch(() => null)
      )
    ).then(pairs => {
      if (cancelled) return
      const next: Record<string, Winrate> = {}
      for (const p of pairs) if (p) next[p[0]] = p[1]
      setData(next)
    })
    return () => { cancelled = true }
  }, [symbol, tick])

  return data
}
