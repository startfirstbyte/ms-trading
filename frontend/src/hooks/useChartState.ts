import { useCallback, useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { IChartingLibraryWidget } from 'charting_library'
import { api } from '../lib/api'

const SAVE_DEBOUNCE_MS  = 2000

export function useChartState(
  widgetRef:    RefObject<IChartingLibraryWidget | null>,
  chartReadyRef: RefObject<boolean>,
  symbol:       string,
) {
  const saveTimerRef      = useRef<ReturnType<typeof setTimeout> | null>(null)
  const symbolRef         = useRef(symbol)
  symbolRef.current       = symbol

  const saveNow = useCallback(() => {
    const widget = widgetRef.current
    if (!widget || !chartReadyRef.current) return
    try {
      widget.save((state: object) => {
        void api.chartStateSave(symbolRef.current, state)
      })
    } catch { /* ignore */ }
  }, [widgetRef, chartReadyRef])

  const scheduleSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(saveNow, SAVE_DEBOUNCE_MS)
  }, [saveNow])

  const attach = useCallback(() => {
    const widget = widgetRef.current
    if (!widget) return

    // Saved drawings are restored at widget construction via `saved_data`
    // (see Chart.tsx) — no async load() here that could reset the layout under
    // the user's hands. We only wire up auto-save of subsequent edits.
    //
    // `onAutoSaveNeeded` is UNRELIABLE for some operations (it does not fire on
    // delete/lock in this TV build) — so a deleted drawing was never persisted
    // and reappeared after reload. `drawing_event` fires on create/move/remove/
    // properties_changed, so subscribing to it makes deletes/locks get saved.
    try {
      widget.subscribe('onAutoSaveNeeded', scheduleSave)
    } catch { /* older TV builds */ }
    try {
      widget.subscribe('drawing_event', scheduleSave)
    } catch { /* older TV builds */ }
  }, [widgetRef, scheduleSave])

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    }
  }, [])

  return { attach, saveNow }
}
