import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'

/**
 * Per-timeframe auto-trigger control. State lives in Redis (server-side),
 * fetched on mount and updated optimistically on toggle.
 * Does NOT affect the manual Analyze button.
 */
export function useAiTfControl(symbol: string) {
  const [tfEnabled, setTfEnabled] = useState<Record<string, boolean>>({})
  const [busy, setBusy]           = useState<Record<string, boolean>>({})

  useEffect(() => {
    let alive = true
    api.getTfConfig(symbol)
      .then(cfg => { if (alive) setTfEnabled(cfg) })
      .catch(() => { /* giữ mặc định rỗng = mọi TF đều bật */ })
    return () => { alive = false }
  }, [symbol])

  const toggle = useCallback(async (res: string) => {
    if (busy[res]) return
    const next = !(tfEnabled[res] ?? true)
    setBusy(b => ({ ...b, [res]: true }))
    try {
      await api.setTfEnabled(symbol, res, next)
      setTfEnabled(e => ({ ...e, [res]: next }))
    } catch { /* ignore — giữ trạng thái cũ */ }
    finally { setBusy(b => ({ ...b, [res]: false })) }
  }, [symbol, tfEnabled, busy])

  return { tfEnabled, busy, toggle }
}
