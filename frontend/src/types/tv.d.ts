declare module '*.module.css' {
  const classes: Record<string, string>
  export default classes
}

/// <reference path="../../../package/charting_library.d.ts" />
/// <reference path="../../../package/datafeed-api.d.ts" />

import type { ChartingLibraryWidgetConstructor } from 'charting_library'

declare global {
  interface Window {
    TradingView: {
      widget: ChartingLibraryWidgetConstructor
    }
  }
}
