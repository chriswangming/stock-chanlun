import { describe, expect, it } from 'vitest'
import {
  buildChanlunOverlayCache,
  resolveDataZoomViewRange,
} from './chartOverlayCore'

describe('chartOverlayCore', () => {
  it('resolveDataZoomViewRange clamps to series length', () => {
    const { viewS, viewE } = resolveDataZoomViewRange(100, [{ startValue: -5, endValue: 200 }])
    expect(viewS).toBe(0)
    expect(viewE).toBe(99)
  })

  it('buildChanlunOverlayCache maps bis to bar indices', () => {
    const dates = ['2024-01-02', '2024-01-03', '2024-01-04']
    const klines = dates.map((d, i) => ({
      date: d,
      open: 10 + i,
      high: 11 + i,
      low: 9 + i,
      close: 10.5 + i,
      volume: 1000,
    }))
    const cache = buildChanlunOverlayCache({
      dates,
      seriesKlines: klines,
      bis: [{
        id: 'b1',
        start: '2024-01-02',
        end: '2024-01-04',
        direction: 'up',
        high: 12,
        low: 9,
        start_price: 9,
        end_price: 12,
      }],
      zhongshus: [],
      signals: [],
      flags: {
        bis: true,
        xians: false,
        zhongshus: false,
        signals: false,
        aiLines: false,
        supportResistance: false,
      },
    })
    expect(cache.bis).toHaveLength(1)
    expect(cache.bis[0]._s).toBe(0)
    expect(cache.bis[0]._e).toBe(2)
  })
})
