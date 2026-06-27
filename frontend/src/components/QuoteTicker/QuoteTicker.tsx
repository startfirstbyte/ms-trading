import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { SYMBOLS } from '../../types/domain'
import styles from './QuoteTicker.module.css'

interface QuoteState {
  price: number | null
  direction: 'up' | 'down' | 'flat'
}

export function QuoteTicker() {
  const [quotes, setQuotes] = useState<Record<string, QuoteState>>({})

  useEffect(() => {
    const prev: Record<string, number> = {}

    const fetchAll = async () => {
      const results = await Promise.allSettled(
        SYMBOLS.map(async ({ name }) => {
          const { bid } = await api.quote(name)
          return { name, bid }
        })
      )
      setQuotes(current => {
        const next = { ...current }
        for (const r of results) {
          if (r.status !== 'fulfilled') continue
          const { name, bid } = r.value
          next[name] = {
            price: bid,
            direction:
              prev[name] == null ? 'flat'
              : bid > prev[name] ? 'up'
              : bid < prev[name] ? 'down'
              : 'flat',
          }
          prev[name] = bid
        }
        return next
      })
    }

    void fetchAll()
    const timer = setInterval(() => { void fetchAll() }, 3_000)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className={styles.ticker}>
      {SYMBOLS.map(({ name, label }) => {
        const q = quotes[name]
        const price = q?.price ?? null
        const formatted =
          price == null ? '—'
          : price.toFixed(price > 1000 ? 2 : price > 10 ? 3 : 5)
        return (
          <div key={name} className={styles.item}>
            <span className={styles.symbol}>{label}</span>
            <span
              className={[
                styles.price,
                q?.direction === 'up'   ? styles.up   : '',
                q?.direction === 'down' ? styles.down : '',
              ].join(' ')}
            >
              {formatted}
            </span>
          </div>
        )
      })}
    </div>
  )
}
