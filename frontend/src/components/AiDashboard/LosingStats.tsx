import { fmtPrice } from '../../hooks/useAiWs'
import { useLosingStats } from '../../hooks/useLosingStats'
import { AI_TF_LABEL } from '../../types/domain'
import type { LosingTrade, TimeframeLossStat } from '../../types/api'
import s from './LosingStats.module.css'

interface Props {
  symbol:  string
  onClose: () => void
}

function cx(...classes: (string | false | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

// "2026-06-21T03:29...Z" → "21/06 10:29" (browser local = VN if máy +7)
function fmtTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function LoserRow({ l }: { l: LosingTrade }) {
  return (
    <div className={s.row}>
      <span className={s.time}>{fmtTime(l.created_at)}</span>
      <span className={cx(s.side, l.signal === 'BUY' ? s.sideBuy : s.sideSell)}>{l.signal}</span>
      <span className={s.prices}>
        {fmtPrice(l.entry)} <span className={s.arrow}>→ SL</span> {fmtPrice(l.stop_loss)}
      </span>
      {l.loss_pct != null && <span className={s.lossPct}>−{l.loss_pct}%</span>}
      {l.regime && <span className={s.regime}>{l.regime}</span>}
    </div>
  )
}

function TfSection({ tf }: { tf: TimeframeLossStat }) {
  const pct = tf.rate != null ? Math.round(tf.rate * 100) : null
  return (
    <div className={s.section}>
      <div className={s.secHead}>
        <span className={s.secTf}>{AI_TF_LABEL[tf.resolution] ?? tf.resolution}</span>
        <span className={s.secStat}>
          <b className={s.lossCount}>{tf.losses} thua</b> · {tf.wins} thắng
          {tf.pending > 0 ? ` · ${tf.pending} chờ` : ''}
          {pct != null && (
            <> · <span className={pct >= 55 ? s.good : pct < 40 ? s.bad : s.mid}>{pct}% thắng</span></>
          )}
        </span>
      </div>
      {tf.losers.length === 0
        ? <div className={s.none}>Không có lệnh thua.</div>
        : tf.losers.map(l => <LoserRow key={l.id} l={l} />)}
    </div>
  )
}

export function LosingStats({ symbol, onClose }: Props) {
  const { data, loading, error } = useLosingStats(symbol, true)

  return (
    <div className={s.overlay}>
      <div className={s.head}>
        <span className={s.title}>
          Thống kê lệnh thua · <b>{symbol}</b>
          {data ? ` · ${data.total_losses} lệnh` : ''}
        </span>
        <button className={s.close} onClick={onClose} title="Đóng">✕</button>
      </div>

      <div className={s.body}>
        {loading && <div className={s.state}>Đang tính… (quét nến 1m, có thể vài giây)</div>}
        {error   && <div className={s.state}>Không tải được thống kê.</div>}
        {!loading && !error && data && data.total_losses === 0 && (
          <div className={s.state}>Chưa có lệnh thua nào.</div>
        )}
        {!loading && !error && data && data.timeframes.map(tf => (
          <TfSection key={tf.resolution} tf={tf} />
        ))}
      </div>
    </div>
  )
}
