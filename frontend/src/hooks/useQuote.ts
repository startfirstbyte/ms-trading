import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'

export function useQuote(symbol: string): number | null {
  const [bid, setBid] = useState<number | null>(null)
  const symbolRef = useRef(symbol)
  useEffect(() => { symbolRef.current = symbol }, [symbol])

  useEffect(() => {
    let cancelled = false
    async function fetch() {
      try {
        const q = await api.quote(symbolRef.current)
        if (!cancelled) setBid(q.bid)
      } catch { /* ignore */ }
    }
    fetch()
    const t = setInterval(fetch, 3000)
    return () => { cancelled = true; clearInterval(t) }
  }, [symbol])

  return bid
}
