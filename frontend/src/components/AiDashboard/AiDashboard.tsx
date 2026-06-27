import React, { useState, useRef } from 'react'
import { useAiWs, fmtPrice, fmtAgeOf } from '../../hooks/useAiWs'
import { useQuote } from '../../hooks/useQuote'
import { useZoneAlert, type ZoneHit } from '../../hooks/useZoneAlert'
import { useAiNotify } from '../../hooks/useAiNotify'
import { useRecentSignal, recentStatus, sigSide, RECENT_WINDOW_MS } from '../../hooks/useRecentSignal'
import { useWinrates } from '../../hooks/useWinrates'
import { useAiTfControl } from '../../hooks/useAiTfControl'
import { AiHistory } from './AiHistory'
import { HelpDoc } from './HelpDoc'
import { LosingStats } from './LosingStats'
import { AI_RESOLUTIONS, AI_TF_LABEL } from '../../types/domain'
import s from './AiDashboard.module.css'

const TF_COLORS: Record<string, string> = {
  '1':  '#a855f7',
  '3':  '#06b6d4',
  '5':  '#f59e0b',
  '15': '#6366f1',
  '60': '#ec4899',
}

interface Props {
  symbol: string
  aiState: ReturnType<typeof useAiWs>
}

function priceDrift(cur: number | null, at: number | null | undefined): number | null {
  if (!cur || !at) return null
  return ((cur - at) / at) * 100
}

function cx(...classes: (string | false | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

export function AiDashboard({ symbol, aiState }: Props) {
  const { cards, cardAges, status, trigger, analyzing } = aiState
  const liveBid    = useQuote(symbol)
  const tfControl  = useAiTfControl(symbol)
  const zoneHits   = useZoneAlert(cards, liveBid, trigger, tfControl.tfEnabled)
  const recent     = useRecentSignal(symbol, cards)
  const winrates   = useWinrates(symbol)
  useAiNotify(symbol, cards)

  const [histRes, setHistRes]     = useState<string | null>(null)
  const [showHelp, setShowHelp]   = useState(false)
  const [showStats, setShowStats] = useState(false)

  // Track per-resolution SL hit timestamp — persist "resolved" state even if price bounces back
  const slHitTimeRef = useRef<Record<string, number>>({})

  return (
    <div className={s.panel}>

      {/* Header */}
      <div className={s.header}>
        <span className={s.symbol}>{symbol}</span>
        <span className={s.aiLabel}>AI SIGNAL</span>
        <button className={s.hHelp} onClick={() => setShowHelp(true)} title="Hướng dẫn phương pháp giao dịch">?</button>
        <button className={s.hHelp} onClick={() => setShowStats(true)} title="Thống kê lệnh thua theo khung">📉</button>
        <div className={s.hSep} />
        <span className={s.hStatus}>{status}</span>
        <button className={s.hBtn} onClick={() => { void trigger(true) }} disabled={analyzing}>
          {analyzing ? 'Analyzing…' : 'Analyze'}
        </button>
      </div>

      {/* Trade Verdict Strip */}
      {AI_RESOLUTIONS.some(r => cards[r]?.trade_status) && (
        <div className={s.verdictStrip}>
          <span className={s.verdictLabel}>Phán quyết</span>
          {AI_RESOLUTIONS.map(res => {
            const ts   = cards[res]?.trade_status
            const note = cards[res]?.trade_note
            return (
              <div
                key={res}
                className={cx(
                  s.verdictChip,
                  ts === 'HOLD'       && s.vChipHold,
                  ts === 'CLOSE'      && s.vChipClose,
                  ts === 'PARTIAL_TP' && s.vChipPartial,
                  !ts                 && s.vChipNone,
                )}
                title={note ? `${AI_TF_LABEL[res] ?? res}: ${note}` : (AI_TF_LABEL[res] ?? res)}
              >
                <span className={s.verdictTf}>{AI_TF_LABEL[res] ?? res}</span>
                <span className={s.verdictStatus}>
                  {ts === 'HOLD' ? '⟳ Giữ' : ts === 'CLOSE' ? '✕ Đóng' : ts === 'PARTIAL_TP' ? '½ Chốt' : '–'}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* Cards */}
      <div className={s.cards}>
        {AI_RESOLUTIONS.map(res => {
          const card   = cards[res]
          const sig     = card?.signal ?? 'WAIT'
          const conv    = card?.conviction ?? 'LOW'
          const age     = cardAges[res] ?? fmtAgeOf(card?.timestamp_ms)
          const isBuy   = sig === 'BUY' || sig === 'BUY_LIMIT' || sig === 'BUY_STOP'
          const isSell  = sig === 'SELL' || sig === 'SELL_LIMIT' || sig === 'SELL_STOP'
          const sigLabel: Record<string, string> = {
            BUY: 'BUY', BUY_LIMIT: 'BUY LMT', BUY_STOP: 'BUY STP',
            SELL: 'SELL', SELL_LIMIT: 'SELL LMT', SELL_STOP: 'SELL STP',
            WAIT: 'WAIT',
          }

          const zoneHit: ZoneHit = zoneHits[res] ?? { active: false, label: '' }
          const isZoneHit = zoneHit.active

          const drift    = priceDrift(liveBid, card?.analysis_bid)
          const absDrift = drift !== null ? Math.abs(drift) : 0
          const isStale  = absDrift > 0.3

          const slBuf = card?.stop_loss != null ? card.stop_loss * 0.0005 : 0
          const isSlHit = liveBid != null && card?.stop_loss != null && (
            (isBuy  && liveBid < card.stop_loss - slBuf) ||
            (isSell && liveBid > card.stop_loss + slBuf)
          )

          // Persist SL-resolved state: once hit, keep "resolved" until a fresh card arrives
          if (isSlHit && !slHitTimeRef.current[res]) {
            slHitTimeRef.current[res] = Date.now()
          }
          // New card from backend (newer timestamp) → fresh analysis, clear the flag
          const cardTs = card?.timestamp_ms ?? 0
          if (slHitTimeRef.current[res] && cardTs > slHitTimeRef.current[res]) {
            delete slHitTimeRef.current[res]
          }
          // wasSlHit stays true even when price bounces back, until backend sends new card
          const wasSlHit = isSlHit || !!slHitTimeRef.current[res]

          const tfOn  = tfControl.tfEnabled[res] ?? true
          const tfBusy = tfControl.busy[res] ?? false

          const lvlCell = (label: string, val: number | null | undefined, cls: string, isKey = false, est?: string) => (
            <div key={label} className={cx(s.levelCell, isKey && s.levelCellKey)}>
              <div className={cx(s.levelLabel, isKey && s.levelLabelKey)}>{label}</div>
              <div className={cx(s.levelVal, val != null ? cls : s.levelEmpty)}>{val != null ? fmtPrice(val) : '–'}</div>
              {isKey && est && val != null && <div className={s.levelEst}>{est}</div>}
            </div>
          )

          return (
            <div
              key={res}
              className={cx(
                s.card,
                isBuy && s.cardBuy,
                isSell && s.cardSell,
                !card && s.cardEmpty,
                isZoneHit && s.cardZoneHit,
                !tfOn && s.cardDisabled,
              )}
              style={{ '--tf-color': TF_COLORS[res] ?? '#3d4454' } as React.CSSProperties}
            >

              {/* Meta */}
              <div className={s.meta}>
                  <span className={cx(s.tf, isBuy && s.tfBuy, isSell && s.tfSell)}>
                    {AI_TF_LABEL[res] ?? res}
                  </span>
                  <span className={cx(s.sigBadge, wasSlHit ? s.sigWait : isBuy ? s.sigBuy : isSell ? s.sigSell : s.sigWait)}
                    style={wasSlHit ? { textDecoration: 'line-through', opacity: 0.5 } : undefined}
                    title={wasSlHit ? 'SL đã chạm — tín hiệu hết hiệu lực' : undefined}
                  >
                    {wasSlHit ? `✕ ${sigLabel[sig] ?? sig}` : (sigLabel[sig] ?? sig)}
                  </span>
                  <span
                    className={cx(s.conv, conv === 'HIGH' ? s.convHigh : conv === 'MEDIUM' ? s.convMed : s.convLow)}
                    title={`Độ tin cậy: ${conv}`}
                  >
                    {[1, 2, 3].map(i => (
                      <i key={i} className={cx(s.convDot, i <= (conv === 'HIGH' ? 3 : conv === 'MEDIUM' ? 2 : 1) && s.convDotOn)} />
                    ))}
                  </span>
                  {card?.regime && (() => {
                    const rg = card.regime
                    const up = rg === 'STRONG_UP' || rg === 'UP'
                    const dn = rg === 'STRONG_DOWN' || rg === 'DOWN'
                    const strong = rg === 'STRONG_UP' || rg === 'STRONG_DOWN'
                    const arrow = up ? (strong ? '⇈' : '↑') : dn ? (strong ? '⇊' : '↓') : '→'
                    return (
                      <span
                        className={cx(s.regime, up ? s.regimeUp : dn ? s.regimeDown : s.regimeFlat)}
                        title={`Xu hướng chủ đạo: ${card.regime_label ?? rg} (${card.regime_score ?? 0}/100)`}
                      >
                        {arrow} {card.regime_label ?? rg}
                      </span>
                    )
                  })()}
                  {isZoneHit && <span className={s.zoneTag}>⚡ {zoneHit.label}</span>}
                  {(() => {
                    const wr = winrates[res]
                    if (!wr || wr.decided < 4) return null
                    const pct = Math.round((wr.rate ?? 0) * 100)
                    return (
                      <span
                        className={s.wr}
                        title={`Hiệu quả gần đây: ${wr.wins} thắng / ${wr.losses} thua (${pct}% · ${wr.decided} lệnh đã quyết)`}
                      >
                        <b className={s.wrW}>{wr.wins}T</b>
                        <span className={s.wrSep}>/</span>
                        <b className={s.wrL}>{wr.losses}L</b>
                      </span>
                    )
                  })()}
                  {card?.win_pct != null && (
                    <span
                      className={cx(s.predBadge, card.win_pct >= 65 ? s.predNew : card.win_pct < 50 ? s.predSame : undefined)}
                      title={`Xác suất thắng AI: ${card.win_pct}%`}
                    >
                      {card.win_pct}%
                    </span>
                  )}
                  <span className={s.age}>{age}</span>
                  <button className={s.histBtn} onClick={() => setHistRes(res)} title="Lịch sử dự báo">🕘</button>
                  {/* Per-TF auto toggle */}
                  <button
                    className={cx(s.tfToggle, !tfOn && s.tfToggleOff)}
                    onClick={() => { void tfControl.toggle(res) }}
                    disabled={tfBusy}
                    title={tfOn ? 'Auto AI đang BẬT cho khung này — click để tắt' : 'Auto AI đang TẮT cho khung này — click để bật'}
                  >
                    {tfBusy ? '…' : tfOn ? '🔔' : '🔕'}
                  </button>
              </div>

              {/* Warnings */}
              {wasSlHit && (
                <div className={cx(s.drift, s.driftHigh)}>
                  <span>✕ SL chạm {fmtPrice(card!.stop_loss)} — hết hiệu lực</span>
                </div>
              )}
              {!wasSlHit && isStale && drift !== null && (
                <div className={cx(s.drift, absDrift > 1 ? s.driftHigh : s.driftMed)}>
                  <span>⚠ Giá dịch {drift > 0 ? '+' : ''}{drift.toFixed(2)}%</span>
                </div>
              )}

              {/* Recent BUY/SELL that has since flipped to WAIT */}
              {(() => {
                const rs = recent[res]
                if (!rs || sig === rs.signal || Date.now() - rs.ts >= RECENT_WINDOW_MS) return null
                const st   = recentStatus(rs, liveBid)
                const mins = Math.max(0, Math.floor((Date.now() - rs.ts) / 60000))
                const rsSide = sigSide(rs.signal)
                const e    = rsSide === 'BUY' ? (rs.entry ?? rs.watchBuy) : (rs.entry ?? rs.watchSell)
                return (
                  <div className={cx(s.recent, rsSide === 'BUY' ? s.recentBuy : s.recentSell)}>
                    <span className={s.recentSig}>⚡ {rs.signal}</span>
                    <span className={s.recentAgo}>{mins}′ trước</span>
                    {e != null && <span className={s.recentEntry}>vào {fmtPrice(e)}</span>}
                    <span className={cx(s.recentStatus, st.kind === 'ok' ? s.rsOk : st.kind === 'late' ? s.rsLate : s.rsGone)}>
                      {st.label}
                    </span>
                  </div>
                )
              })()}

              {/* Info */}
              <div className={s.info}>
                {card?.trigger ? (
                  <div className={s.trigInline}>
                    <span className={cx(s.trigArrow, isBuy && s.trigArrowBuy, isSell && s.trigArrowSell)}>
                      {isBuy ? (sig === 'BUY_STOP' ? '⤴' : '↑') : isSell ? (sig === 'SELL_STOP' ? '⤵' : '↓') : '→'}
</span>
                    <span className={s.trigText} title={card.trigger}>{card.trigger}</span>
                  </div>
                ) : !card ? (
                  <span className={s.emptyHint}>Click Analyze to start</span>
                ) : null}

                {card?.trade_status && (
                  <div className={cx(
                    s.tradeBadge,
                    card.trade_status === 'HOLD'       && s.tradeHold,
                    card.trade_status === 'CLOSE'      && s.tradeClose,
                    card.trade_status === 'PARTIAL_TP' && s.tradePartial,
                  )}>
                    <span className={s.tradeIcon}>
                      {card.trade_status === 'HOLD' ? '⟳' : card.trade_status === 'CLOSE' ? '✕' : '½'}
                    </span>
                    <span>{card.trade_status === 'HOLD' ? 'Giữ lệnh' : card.trade_status === 'CLOSE' ? 'Đóng lệnh' : 'Chốt 1 phần'}</span>
                    {card.trade_note && <span className={s.tradeNote}>— {card.trade_note}</span>}
                  </div>
                )}

                {card?.analysis && card.analysis !== '—' && (
                  <p className={s.note} title={card.analysis}>{card.analysis}</p>
                )}
              </div>

              {/* Level rows */}
              <div className={s.levels}>
                {lvlCell('Entry', card?.entry_zone, s.priceVal)}
                {lvlCell('SL',    card?.stop_loss,  s.priceValSl)}
              </div>
              <div className={s.levels}>
                {lvlCell('TP1', card?.target1, s.priceValTp)}
                {lvlCell('TP2', card?.target2, s.priceValTp)}
                {lvlCell('TP3', card?.target3, s.priceValTp)}
              </div>
            </div>
          )
        })}
      </div>

      {histRes && (
        <AiHistory symbol={symbol} resolution={histRes} onClose={() => setHistRes(null)} />
      )}
      {showHelp && <HelpDoc onClose={() => setShowHelp(false)} />}
      {showStats && <LosingStats symbol={symbol} onClose={() => setShowStats(false)} />}
    </div>
  )
}
