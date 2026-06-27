import { useEffect, useMemo, useRef } from 'react'
import type { AICard } from '../types/api'

const COOLDOWN = 3 * 60_000   // 3 phút giữa 2 lần trigger cùng level

export interface ZoneHit {
  active: boolean
  label:  string
}

// Collect all price levels from a card worth watching
function getLevels(card: AICard): Array<{ price: number; label: string }> {
  const out: Array<{ price: number; label: string }> = []
  if (card.watch_buy  != null) out.push({ price: card.watch_buy,  label: 'BUY ZONE'  })
  if (card.watch_sell != null) out.push({ price: card.watch_sell, label: 'SELL ZONE' })
  if (card.key_level  != null) out.push({ price: card.key_level,  label: 'KEY LEVEL' })
  if (card.entry_zone != null) out.push({ price: card.entry_zone, label: 'ENTRY'     })
  return out
}

// Check if price crossed a level between prevBid and curBid
function crossed(prev: number, cur: number, level: number): boolean {
  return (prev < level && cur >= level) || (prev > level && cur <= level)
}

export function useZoneAlert(
  cards:     Record<string, AICard>,
  liveBid:   number | null,
  trigger:   (force?: boolean, resolution?: string) => Promise<void>,
  tfEnabled: Record<string, boolean>,
): Record<string, ZoneHit> {
  const prevBidRef   = useRef<number | null>(null)
  const cooldownRef  = useRef<Record<string, number>>({})   // key=`${res}:${label}`
  const activeHitRef = useRef<Record<string, ZoneHit>>({})  // currently displaying hits

  // Detect crossings and update activeHit
  useEffect(() => {
    if (liveBid == null) return
    const prev = prevBidRef.current
    prevBidRef.current = liveBid
    if (prev == null) return

    const now = Date.now()

    for (const [res, card] of Object.entries(cards)) {
      for (const lvl of getLevels(card)) {
        if (!crossed(prev, liveBid, lvl.price)) continue

        const key      = `${res}:${lvl.label}`
        const lastFire = cooldownRef.current[key] ?? 0
        if (now - lastFire < COOLDOWN) continue

        cooldownRef.current[key] = now

        // Mark this card as hit so it blinks
        activeHitRef.current = {
          ...activeHitRef.current,
          [res]: { active: true, label: lvl.label },
        }

        // Trigger AI re-analysis chỉ khi TF chưa bị tắt
        if ((tfEnabled[res] ?? true) !== false) void trigger(true, res)

        // Clear blink after 30s so card doesn't blink forever
        setTimeout(() => {
          activeHitRef.current = {
            ...activeHitRef.current,
            [res]: { active: false, label: '' },
          }
        }, 30_000)
      }
    }
  }, [liveBid, cards, trigger])

  // Expose current hits as stable object for render
  // useMemo re-runs when liveBid changes so the component re-renders to pick up ref updates
  const hits = useMemo<Record<string, ZoneHit>>(() => {
    return { ...activeHitRef.current }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveBid])

  return hits
}
