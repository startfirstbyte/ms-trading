import { fmtPrice } from '../../hooks/useAiWs'
import { useAiHistory } from '../../hooks/useAiHistory'
import { AI_TF_LABEL } from '../../types/domain'
import type { AiPrediction } from '../../types/api'
import s from './AiHistory.module.css'

interface Props {
  symbol:     string
  resolution: string
  onClose:    () => void
}

function cx(...classes: (string | false | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

// "2026-06-20T11:06:03.331+00:00" → "11:06 · 20/06"
function fmtStamp(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())} · ${p(d.getDate())}/${p(d.getMonth() + 1)}`
}

const EVENT_LABEL: Record<string, string> = {
  manual:        'Thủ công',
  candle_close:  'Đóng nến',
  zone_touch:    'Chạm vùng',
  auto:          'Tự động',
}

function convDots(conv: AiPrediction['conviction']) {
  const lvl = conv === 'HIGH' ? 3 : conv === 'MEDIUM' ? 2 : 1
  const cls = conv === 'HIGH' ? s.convHigh : conv === 'MEDIUM' ? s.convMed : s.convLow
  return (
    <span className={cx(s.conv, cls)} title={`Độ tin cậy: ${conv ?? 'LOW'}`}>
      {[1, 2, 3].map(i => <i key={i} className={cx(s.convDot, i <= lvl && s.convDotOn)} />)}
    </span>
  )
}

function Row({ p }: { p: AiPrediction }) {
  const isBuy  = p.signal === 'BUY'
  const isSell = p.signal === 'SELL'
  // Legacy rows: the LLM sometimes put "HOLD" in signal (means "giữ lệnh").
  const isHold = (p.signal as string) === 'HOLD'

  const levels: { label: string; val: number | null; cls: string }[] = [
    { label: 'Entry',  val: p.entry_zone, cls: s.lvlEntry },
    { label: 'TP',     val: p.target,     cls: s.lvlTp },
    { label: 'SL',     val: p.stop_loss,  cls: s.lvlSl },
    { label: 'Key',    val: p.key_level,  cls: s.lvlKey },
  ].filter(l => l.val != null)

  return (
    <div className={cx(s.row, isBuy && s.rowBuy, isSell && s.rowSell)}>
      <div className={s.rowHead}>
        <span className={s.stamp}>{fmtStamp(p.created_at)}</span>
        <span className={cx(s.sig, isBuy ? s.sigBuy : isSell ? s.sigSell : isHold ? s.sigHold : s.sigWait)}>
          {isHold ? 'GIỮ' : p.signal}
        </span>
        {convDots(p.conviction)}
        {p.trigger_event && (
          <span className={s.event}>{EVENT_LABEL[p.trigger_event] ?? p.trigger_event}</span>
        )}
        {p.prediction_updated === true && (
          <span className={cx(s.upd, s.updNew)} title={p.update_reason ?? ''}>↻ Mới</span>
        )}
      </div>

      {p.trigger && <div className={s.trig}>{p.trigger}</div>}

      {levels.length > 0 && (
        <div className={s.levels}>
          {levels.map(l => (
            <span key={l.label} className={s.lvl}>
              <span className={s.lvlLabel}>{l.label}</span>
              <span className={cx(s.lvlVal, l.cls)}>{fmtPrice(l.val)}</span>
            </span>
          ))}
        </div>
      )}

      {p.analysis && p.analysis !== '—' && <p className={s.note}>{p.analysis}</p>}
    </div>
  )
}

export function AiHistory({ symbol, resolution, onClose }: Props) {
  const { data, loading, error } = useAiHistory(symbol, resolution, 10)

  return (
    <div className={s.overlay}>
      <div className={s.head}>
        <span className={s.title}>
          Lịch sử dự báo · <b>{symbol}</b> · {AI_TF_LABEL[resolution] ?? resolution}
        </span>
        <button className={s.close} onClick={onClose} title="Đóng">✕</button>
      </div>

      <div className={s.body}>
        {loading && <div className={s.state}>Đang tải…</div>}
        {error   && <div className={s.state}>Không tải được lịch sử.</div>}
        {!loading && !error && data.length === 0 && (
          <div className={s.state}>Chưa có dự báo nào cho khung này.</div>
        )}
        {!loading && !error && data.map((p, i) => <Row key={p.created_at + i} p={p} />)}
      </div>
    </div>
  )
}
