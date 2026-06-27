import { useEffect, useRef } from 'react'
import type { AICard } from '../types/api'

// Notify when a card transitions to an important state
// - trade_status: null/HOLD → CLOSE or PARTIAL_TP
// - prediction_updated: false → true (signal changed direction)

const TF_LABEL: Record<string, string> = { '1':'1m', '3':'3m', '5':'5m', '15':'15m', '60':'1H' }

function requestPermission() {
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    void Notification.requestPermission()
  }
}

function notify(title: string, body: string, urgency: 'high' | 'normal' = 'normal') {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  try {
    const n = new Notification(title, {
      body,
      icon: '/favicon.ico',
      tag: title,                  // dedup: same tag replaces previous
      requireInteraction: urgency === 'high',
    })
    // Auto-close low-urgency after 8s
    if (urgency === 'normal') setTimeout(() => n.close(), 8000)
  } catch { /* ignore */ }
}

export function useAiNotify(symbol: string, cards: Record<string, AICard>) {
  const prevRef = useRef<Record<string, AICard>>({})

  // Ask for permission once on mount
  useEffect(() => { requestPermission() }, [])

  useEffect(() => {
    const prev = prevRef.current

    for (const [res, card] of Object.entries(cards)) {
      const old  = prev[res]
      const tf   = TF_LABEL[res] ?? res

      if (!old) {
        // First time seeing this card — skip (no transition)
        continue
      }

      // ── Trade status changed ───────────────────────────────────────────────
      if (card.trade_status !== old.trade_status) {
        if (card.trade_status === 'CLOSE') {
          notify(
            `⚠ ${symbol} ${tf} — Đóng lệnh!`,
            card.trade_note ?? `${old.signal} signal đảo chiều — đóng lệnh ngay`,
            'high',
          )
        } else if (card.trade_status === 'PARTIAL_TP') {
          notify(
            `${symbol} ${tf} — Chốt 1 phần`,
            card.trade_note ?? 'Giá gần TP, xem xét chốt một phần',
            'normal',
          )
        }
      }

      // ── Prediction updated with direction change ───────────────────────────
      if (
        card.prediction_updated === true &&
        old.prediction_updated !== true &&
        card.signal !== old.signal &&
        card.signal !== 'WAIT'
      ) {
        notify(
          `${symbol} ${tf} — Tín hiệu mới: ${card.signal}`,
          card.update_reason ?? card.trigger ?? `Prediction cập nhật: ${old.signal} → ${card.signal}`,
          card.conviction === 'HIGH' ? 'high' : 'normal',
        )
      }

      // ── Signal flipped from BUY→SELL or SELL→BUY (even without trade_status) ─
      if (
        old.signal !== 'WAIT' &&
        card.signal !== 'WAIT' &&
        old.signal !== card.signal
      ) {
        notify(
          `${symbol} ${tf} — Đảo chiều: ${old.signal} → ${card.signal}`,
          card.trigger ?? card.analysis ?? '',
          'high',
        )
      }
    }

    prevRef.current = cards
  }, [cards, symbol])
}
