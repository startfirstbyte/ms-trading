import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { IChartingLibraryWidget, EntityId } from 'charting_library'
import type { MSResult, MSWave, MSChannel, MSWedge, MSRuleSignal, PriceChannel } from '../types/api'
import { MS_LOOKBACK } from '../types/domain'

// ── Notification helper ───────────────────────────────────────────────────────

function _notify(title: string, body: string) {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  try {
    const n = new Notification(title, { body, icon: '/favicon.ico', tag: title, requireInteraction: true })
    setTimeout(() => n.close(), 15000)
  } catch { /* ignore */ }
}

function _requestNotifyPermission() {
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    void Notification.requestPermission()
  }
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface MSSnapshot {
  computed_at: number
  pattern:     string
  confidence:  number
  waves:       MSWave[]
  draw_waves?: MSWave[]   // zigzag mịn cho chart (tách khỏi waves cấu trúc)
  channel:     MSChannel | null
  wedge:       MSWedge | null | undefined
  structure:   { trend: string; bos_level: number | null; event: string } | null
}

// ── Shape helper ──────────────────────────────────────────────────────────────

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.substring(0, 2), 16)
  const g = parseInt(h.substring(2, 4), 16)
  const b = parseInt(h.substring(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

type ShapePoint = { time: number; price: number }

// TradingView v31 returns Promise<EntityId> — store the promise, resolve on removal
function addShape(
  chart: ReturnType<IChartingLibraryWidget['chart']>,
  pts:   ShapePoint | ShapePoint[],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  shape: any,
  overrides: Record<string, unknown> = {}
): Promise<EntityId | null> {
  try {
    // lock:false QUAN TRỌNG — removeEntity thất bại trên shape lock:true trong TV build
    // này → shape cũ tích tụ. disableSelection vẫn chặn người dùng chọn/kéo.
    const opts = { shape, lock: false, disableSelection: true, disableSave: true, disableUndo: true, overrides }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const raw = Array.isArray(pts)
      ? chart.createMultipointShape(pts as any, opts)
      : chart.createShape(pts as any, opts)
    return Promise.resolve(raw as unknown as EntityId | null)
  } catch {
    return Promise.resolve(null)
  }
}

// ── Chip ──────────────────────────────────────────────────────────────────────

export interface MSChip { text: string; cls: '' | 'up' | 'down' }

function buildChip(snap: MSSnapshot): MSChip {
  const pat   = snap.pattern ?? ''
  const trend = snap.structure?.trend ?? 'ranging'
  const dir   = snap.channel?.direction ?? 'flat'
  const arrow = dir === 'up' ? '↑' : dir === 'down' ? '↓' : '→'
  const pct   = Math.round((snap.confidence ?? 0) * 100)

  let kind: string
  if (pat.includes('bos'))          kind = 'BOS'
  else if (pat.includes('choch'))   kind = 'CHOCH'
  else if (pat.includes('channel')) kind = 'CH'
  else if (pat.includes('range'))   kind = 'RNG'
  else if (trend === 'bullish')     kind = 'BULL'
  else if (trend === 'bearish')     kind = 'BEAR'
  else                              kind = 'RNG'

  const cls: MSChip['cls'] = dir === 'up' ? 'up' : dir === 'down' ? 'down' : ''
  return { text: `MS: ${kind} ${arrow}${pct > 0 ? ` ${pct}%` : ''}`, cls }
}

// ── Draw one snapshot ─────────────────────────────────────────────────────────

// alpha: 1.0 = newest (full color), 0.25 = oldest (faded)
function drawSnapshot(
  chart:  ReturnType<IChartingLibraryWidget['chart']>,
  snap:   MSSnapshot,
  alpha:  number,
  isLatest: boolean
): Promise<EntityId | null>[] {
  const ids: Promise<EntityId | null>[] = []
  const { waves = [], draw_waves, channel: ch, structure } = snap
  // Vẽ nếu có channel HOẶC đủ pivot để vẽ zigzag (draw_waves ưu tiên, fallback waves).
  // Trước đây chỉ xét waves → TF pattern=none/channel=null (vd 1m) bị bỏ vẽ dù có draw_waves.
  const hasZz = (draw_waves?.length ?? 0) >= 2 || waves.length >= 2
  if (!ch && !hasZz) return ids

  const toSec = (ms: number) => Math.floor(ms / 1000)

  // Channel/range giờ vẽ riêng bởi drawChannels() (store /api/channels có lifecycle
  // editing/committed), không vẽ trong snapshot nữa.

  // 2. ZigZag lines — dùng draw_waves (zigzag mịn) để không "đứt" ở vùng ranging;
  //    fallback về waves cấu trúc nếu snapshot cũ chưa có draw_waves.
  const zz = (draw_waves && draw_waves.length >= 2) ? draw_waves : waves
  if (zz.length >= 2) {
    for (let i = 0; i < zz.length - 1; i++) {
      const a = zz[i]
      const b = zz[i + 1]
      const segColor = a.type === 'high' ? '#ef5350' : '#26a69a'
      ids.push(addShape(chart, [
        { time: toSec(a.time), price: a.price },
        { time: toSec(b.time), price: b.price },
      ], 'trend_line', { linecolor: segColor, linewidth: isLatest ? 2 : 1, linestyle: 0 }))
    }
  }

  // 3. Swing labels — only on latest snapshot
  if (isLatest) {
    for (const w of waves) {
      const ww = w as MSWave & { label: string; type?: string }
      const isBosLabel = ww.label === 'BOS' || ww.label === 'CHOCH'
      ids.push(addShape(chart,
        { time: toSec(ww.time), price: ww.price },
        'text', {
          text:      ww.label,
          fontsize:  isBosLabel ? 13 : 10,
          bold:      isBosLabel,
          color:     isBosLabel ? '#f59e0b' : ww.type === 'high' ? '#ef5350' : '#26a69a',
          fixedSize: true,
        }))
    }
  }

  // 4. BOS/CHOCH horizontal level (latest only)
  if (isLatest && structure?.bos_level && waves.length) {
    const lastWave = waves[waves.length - 1]
    ids.push(addShape(chart,
      { time: toSec(lastWave.time), price: structure.bos_level },
      'horizontal_line', { linecolor: '#f59e0b', linewidth: 1, linestyle: 2, showPrice: true }))
  }

void alpha  // reserved for future transparency API support
  return ids
}

// ── Channel store rendering ─────────────────────────────────────────────────────
// editing  → đậm, nét liền, có nhãn đo lường (channel đang sống, còn re-fit).
// committed → mờ, nét đứt, đánh dấu điểm phá biên (leg đã đóng băng).

function drawChannels(
  chart:    ReturnType<IChartingLibraryWidget['chart']>,
  channels: PriceChannel[]
): Promise<EntityId | null>[] {
  const ids: Promise<EntityId | null>[] = []
  const toSec = (ms: number) => Math.floor(ms / 1000)

  for (const pc of channels) {
    const ch = pc.channel
    if (!ch || !ch.time_start || !ch.time_end) continue

    // 3 trạng thái: confirmed (đậm nhất) > editing (vừa) > committed (mờ nét đứt).
    const editing      = pc.status === 'editing'
    const confirmed    = pc.status === 'confirmed'
    const committed    = pc.status === 'committed'
    const chColor      = ch.direction === 'up' ? '#26a69a' : ch.direction === 'down' ? '#ef5350' : '#787b86'
    // committed dùng màu pha loãng alpha — nét mảnh/nét đứt trước đây vẫn giữ màu
    // gốc 100% nên nhìn không thực sự mờ; giờ viền cũng nhạt đi để rõ là "channel cũ".
    const lineColor    = committed ? hexToRgba(chColor, 0.35) : chColor
    const lw           = committed ? 1 : 2          // committed mảnh, editing/confirmed đậm
    const lstyle       = committed ? 2 : 0          // committed nét đứt, còn lại nét liền
    // fill: confirmed đậm nhất (macro xác nhận) → editing vừa → committed gần như trong suốt.
    const transparency = confirmed ? 72 : editing ? 82 : 97
    const fillBg       = !committed                 // committed bỏ fill, chỉ viền mảnh nét đứt
    const showMid      = !committed

    if (ch.channel_type === 'range') {
      ids.push(addShape(chart, [
        { time: toSec(ch.time_start), price: ch.upper },
        { time: toSec(ch.time_end),   price: ch.lower },
      ], 'rectangle', {
        color:           lineColor,
        linewidth:       lw,
        linestyle:       lstyle,
        fillBackground:  fillBg,
        backgroundColor: chColor,
        transparency,
      }))
    } else {
      ids.push(addShape(chart, [
        { time: toSec(ch.time_start), price: ch.lower_start ?? ch.lower },
        { time: toSec(ch.time_end),   price: ch.lower_end   ?? ch.lower },
        { time: toSec(ch.time_end),   price: ch.upper_end   ?? ch.upper },
      ], 'parallel_channel', {
        linecolor:       lineColor,
        linewidth:       lw,
        linestyle:       lstyle,
        showMidline:     showMid,
        midlinecolor:    chColor,
        midlinestyle:    1,
        midlinewidth:    1,
        fillBackground:  fillBg,
        backgroundColor: chColor,
        transparency,
        extendLeft:      false,
        extendRight:     false,
      }))
    }

    // Nhãn đo lường — editing & confirmed (confirmed có dấu ✓ phân biệt xu hướng đã xác nhận)
    if (editing || confirmed) {
      const width   = ch.width ?? (ch.upper - ch.lower)
      const pctText = ch.width_pct != null
        ? ch.width_pct.toFixed(2)
        : (ch.mid ? (width / ch.mid * 100).toFixed(2) : '0')
      const ampText = width >= 50 ? width.toFixed(1) : width.toFixed(2)
      const posPct  = Math.round((ch.pos ?? 0.5) * 100)
      const edge    = ch.upper_end ?? ch.upper
      const prefix  = confirmed ? '✓ ' : ''
      ids.push(addShape(chart,
        { time: toSec(ch.time_end), price: edge },
        'text', {
          text:      `${prefix}Biên độ ${ampText} (${pctText}%)  ·  Vị trí ${posPct}%`,
          color:     chColor,
          fontsize:  11,
          bold:      true,
          fixedSize: true,
        }))
    }

    // Committed — đánh dấu điểm phá biên (text trung tính, không dùng mũi tên)
    if (committed && pc.break_price != null && pc.break_time != null) {
      ids.push(addShape(chart,
        { time: toSec(pc.break_time), price: pc.break_price },
        'text', {
          text:      pc.break_side === 'upper' ? '✕ phá trên' : '✕ phá dưới',
          color:     pc.break_side === 'upper' ? '#26a69a' : '#ef5350',
          fontsize:  10,
          fixedSize: true,
        }))
    }
  }
  return ids
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useMarketStructure(
  widgetRef:    RefObject<IChartingLibraryWidget | null>,
  chartReadyRef: RefObject<boolean>,
  symbol:       string,
  resolution:   string,
) {
  const [chip,   setChip]   = useState<MSChip>({ text: 'MS', cls: '' })
  const [signal, setSignal] = useState<MSRuleSignal>({ signal: 'WAIT', pos: 0.5, labels: [] })
  const prevSignalRef = useRef<'BUY' | 'SELL' | 'WAIT'>('WAIT')

  useEffect(() => { _requestNotifyPermission() }, [])

  const shapeIdsRef = useRef<Promise<EntityId | null>[]>([])
  const drawnSigRef = useRef<string | null>(null)
  const abortRef    = useRef<AbortController | null>(null)
  const timerRef    = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Clear all shapes ───────────────────────────────────────────────────────

  const clearShapes = useCallback(() => {
    const widget  = widgetRef.current
    const pending = shapeIdsRef.current
    shapeIdsRef.current = []
    drawnSigRef.current = null
    if (!widget || !pending.length) return
    let chart: ReturnType<IChartingLibraryWidget['chart']>
    try { chart = widget.chart() } catch { return }
    Promise.all(pending).then(ids => {
      for (const id of ids) {
        if (id != null) try { chart.removeEntity(id) } catch { /* already gone */ }
      }
    }).catch(() => { /* ignore */ })
  }, [widgetRef])

  // ── Draw all snapshots ─────────────────────────────────────────────────────

  const drawAll = useCallback((snapshots: MSSnapshot[], channels: PriceChannel[] = []) => {
    if (!chartReadyRef.current || !widgetRef.current) return
    if (!snapshots.length && !channels.length) return

    let chart: ReturnType<IChartingLibraryWidget['chart']>
    try { chart = widgetRef.current.chart() } catch { return }

    // snapshots[0] = newest, snapshots[N-1] = oldest
    const n    = snapshots.length
    const ids: Promise<EntityId | null>[] = []

    // Channels vẽ TRƯỚC (nền có fill) → zigzag/label vẽ SAU sẽ nằm TRÊN, không bị fill
    // của confirmed/editing che. (z-order TradingView: shape vẽ sau nằm trên.)
    ids.push(...drawChannels(chart, channels))

    for (let i = 0; i < n; i++) {
      const snap     = snapshots[i]
      const isLatest = i === 0
      const alpha    = 1.0 - (i / n) * 0.75   // 1.0 → 0.25
      ids.push(...drawSnapshot(chart, snap, alpha, isLatest))
    }

    // Nối ranh giới giữa các snapshot window kề nhau: pivot mới nhất của window cũ
    // → pivot cũ nhất của window mới, tránh "đứt gãy" zigzag tại biên window.
    for (let i = 0; i < n - 1; i++) {
      const newer = snapshots[i].draw_waves?.length     ? snapshots[i].draw_waves!     : snapshots[i].waves       // window mới hơn
      const older = snapshots[i + 1].draw_waves?.length ? snapshots[i + 1].draw_waves! : snapshots[i + 1].waves   // window cũ hơn
      if (!newer?.length || !older?.length) continue
      const a = older[older.length - 1]      // pivot mới nhất của window cũ
      const b = newer[0]                     // pivot cũ nhất của window mới
      if (!a || !b || a.time >= b.time) continue
      // Guard: chỉ nối nếu hai window thực sự kề nhau. Nếu khoảng cách > tổng span
      // của hai window (vd snapshot cũ lạc vài ngày) → BỎ, tránh đường ghost dài.
      const olderSpan = Math.abs(older[older.length - 1].time - older[0].time)
      const newerSpan = Math.abs(newer[newer.length - 1].time - newer[0].time)
      if (b.time - a.time > olderSpan + newerSpan + 60_000) continue
      const segColor = (a as MSWave).type === 'high' ? '#ef5350' : '#26a69a'
      ids.push(addShape(chart, [
        { time: Math.floor(a.time / 1000), price: a.price },
        { time: Math.floor(b.time / 1000), price: b.price },
      ], 'trend_line', { linecolor: segColor, linewidth: 1, linestyle: 0 }))
    }

    shapeIdsRef.current = ids
    if (snapshots.length) setChip(buildChip(snapshots[0]))
  }, [chartReadyRef, widgetRef])

  // ── Fetch snapshots from DB + compute latest ───────────────────────────────

  const fetchAndDraw = useCallback(async () => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
    const ctrl = new AbortController()
    abortRef.current = ctrl
    const bars = MS_LOOKBACK[resolution] ?? 150

    try {
      // 1. Compute latest TRƯỚC (cũng tạo/cập nhật editing channel trong DB).
      //    Phải xong compute rồi mới fetch channels/snapshots, nếu không lần đầu xem
      //    1 symbol:TF (chưa có channel) sẽ race → channels trả [] → không vẽ kênh.
      const computeRes = await fetch(
        `http://localhost:8000/api/ms/compute?symbol=${symbol}&resolution=${resolution}&bars=${bars}`,
        { method: 'POST', signal: ctrl.signal }
      )
      if (ctrl.signal.aborted) return
      const latest: MSResult = computeRes.ok ? await computeRes.json() : null
      if (!latest) return

      // 2. Snapshots + persisted channels (đọc sau khi compute đã ghi DB)
      const [historyRes, channelsRes] = await Promise.all([
        fetch(
          `http://localhost:8000/api/ms/snapshots?symbol=${symbol}&resolution=${resolution}&limit=4`,
          { signal: ctrl.signal }
        ),
        fetch(
          `http://localhost:8000/api/channels?symbol=${symbol}&resolution=${resolution}`,
          { signal: ctrl.signal }
        ),
      ])

      if (ctrl.signal.aborted) return

      const history:   MSSnapshot[]   = historyRes.ok   ? await historyRes.json()   : []
      const channels:  PriceChannel[] = channelsRes.ok  ? await channelsRes.json()  : []

      // Merge: latest first, then 1 older from DB (dedup by computed_at)
      const latestSnap: MSSnapshot = {
        computed_at: (latest as unknown as Record<string,number>).computed_at ?? Date.now(),
        pattern:     latest.pattern,
        confidence:  latest.confidence,
        waves:       latest.waves ?? [],
        draw_waves:  latest.draw_waves ?? [],
        channel:     latest.channel ?? null,
        wedge:       latest.wedge ?? null,
        structure:   latest.structure ?? null,
      }

      const seen = new Set<number>([latestSnap.computed_at])
      const snapshots: MSSnapshot[] = [latestSnap]
      for (const h of history) {
        if (!seen.has(h.computed_at)) {
          seen.add(h.computed_at)
          snapshots.push(h)
        }
        if (snapshots.length >= 4) break   // current + 3 historical
      }

      // Signature — gồm cả lifecycle channel để commit/leg mới trigger redraw
      const chSig = channels.map(c => `${c.id}:${c.status}:${c.channel?.upper_end ?? 0}`).join(',')
      const sig = `${latestSnap.pattern}|${latestSnap.confidence}|${latestSnap.waves.length}|${latestSnap.draw_waves?.length ?? 0}|${chSig}`
      if (sig === drawnSigRef.current) {
        setChip(buildChip(latestSnap))
        return
      }

      clearShapes()
      drawAll(snapshots, channels)
      drawnSigRef.current = sig

      // Rule-based signal + notification
      const rs = (latest as MSResult).rule_signal
      if (rs) {
        setSignal(rs)
        const prev = prevSignalRef.current
        if (rs.signal !== 'WAIT' && rs.signal !== prev) {
          const tf = { '1':'1m','3':'3m','5':'5m','15':'15m','60':'1H' }[resolution] ?? resolution
          _notify(
            `${rs.signal === 'BUY' ? '🟢' : '🔴'} ${symbol} ${tf} — ${rs.signal}`,
            `Kênh pos=${(rs.pos * 100).toFixed(0)}%  |  MS: ${rs.labels.join(' ')}`,
          )
        }
        prevSignalRef.current = rs.signal
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') console.warn('[MS] fetch error:', e)
    } finally {
      abortRef.current = null
    }
  }, [symbol, resolution, clearShapes, drawAll])

  // ── Auto-refresh every 5 min; run immediately only if chart already ready ────
  // (covers HMR remount where onChartReady won't fire again)

  useEffect(() => {
    if (chartReadyRef.current) void fetchAndDraw()
    timerRef.current = setInterval(() => { void fetchAndDraw() }, 300_000)  // 5 min
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      if (abortRef.current) abortRef.current.abort()
      // Xoá shape khi unmount / HMR re-mount / đổi symbol-res → tránh tích tụ ghost
      clearShapes()
    }
  }, [fetchAndDraw, clearShapes])

  // ── Public API ─────────────────────────────────────────────────────────────

  const redraw = useCallback(() => {
    clearShapes()
    void fetchAndDraw()
  }, [clearShapes, fetchAndDraw])

  return { chip, signal, redraw, clearShapes }
}
