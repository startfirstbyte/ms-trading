import { useRef, useState } from 'react'
import { Toolbar } from './components/Toolbar/Toolbar'
import { Chart } from './components/Chart/Chart'
import { useUrlState } from './hooks/useUrlState'
import type { Resolution } from './types/api'

export function App() {
  const { symbol, resolution, setSymbol, setResolution } = useUrlState()
  const recalcRef = useRef<(() => void) | null>(null)

  const [showSrZones, setShowSrZones] = useState(
    () => localStorage.getItem('showSrZones') !== '0'   // mặc định bật
  )
  const toggleSrZones = () => {
    setShowSrZones(v => {
      const next = !v
      localStorage.setItem('showSrZones', next ? '1' : '0')
      return next
    })
  }

  return (
    <div className="app">
      <div className="main">
        <Toolbar
          symbol={symbol}
          resolution={resolution as Resolution}
          onSymbol={setSymbol}
          onResolution={setResolution}
          onRecalculate={() => recalcRef.current?.()}
          showSrZones={showSrZones}
          onToggleSrZones={toggleSrZones}
        />
        <Chart
          symbol={symbol}
          resolution={resolution as Resolution}
          recalcRef={recalcRef}
          showSrZones={showSrZones}
        />
      </div>
    </div>
  )
}
