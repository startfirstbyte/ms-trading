import { SYMBOLS, TIMEFRAMES } from '../../types/domain'
import type { Resolution } from '../../types/api'
import { MethodInfo } from '../MethodInfo/MethodInfo'
import styles from './Toolbar.module.css'

interface Props {
  symbol: string
  resolution: Resolution
  onSymbol: (s: string) => void
  onResolution: (r: Resolution) => void
  onRecalculate: () => void
}

export function Toolbar({ symbol, resolution, onSymbol, onResolution, onRecalculate }: Props) {
  return (
    <header className={styles.toolbar}>
      <select
        className={styles.symbolSelect}
        value={symbol}
        onChange={e => onSymbol(e.target.value)}
      >
        {SYMBOLS.map(s => (
          <option key={s.name} value={s.name}>{s.label}</option>
        ))}
      </select>

      <div className={styles.separator} />

      <div className={styles.group}>
        {TIMEFRAMES.map(tf => (
          <button
            key={tf.resolution}
            className={[styles.btn, resolution === tf.resolution ? styles.active : ''].join(' ')}
            onClick={() => onResolution(tf.resolution)}
          >
            {tf.label}
          </button>
        ))}
      </div>

      <div className={styles.separator} />

      <button
        className={[styles.btn, styles.recalcBtn].join(' ')}
        onClick={onRecalculate}
        title="Recalculate market structure"
      >
        ↺ MS
      </button>

      <div className={styles.separator} />

      <MethodInfo />

      <div className={styles.spacer} />
    </header>
  )
}
