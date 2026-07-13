import { useRef } from 'react'
import { Toolbar } from './components/Toolbar/Toolbar'
import { Chart } from './components/Chart/Chart'
import { useUrlState } from './hooks/useUrlState'
import type { Resolution } from './types/api'

export function App() {
  const { symbol, resolution, setSymbol, setResolution } = useUrlState()
  const recalcRef = useRef<(() => void) | null>(null)

  return (
    <div className="app">
      <div className="main">
        <Toolbar
          symbol={symbol}
          resolution={resolution as Resolution}
          onSymbol={setSymbol}
          onResolution={setResolution}
          onRecalculate={() => recalcRef.current?.()}
        />
        <Chart
          symbol={symbol}
          resolution={resolution as Resolution}
          recalcRef={recalcRef}
        />
      </div>
    </div>
  )
}
