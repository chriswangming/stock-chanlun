import { describe, expect, it } from 'vitest'
import {
  buildChanlunGraphicChildren,
  buildChanlunOverlayCache,
  CHANLUN_OVERLAY_THEME_PC,
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
        mergedKlines: false,
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

  it('buildChanlunOverlayCache + graphic draw merged-kline boxes', () => {
    const dates = ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05', '2024-01-06']
    const klines = dates.map((d, i) => ({
      date: d, open: 10 + i, high: 11 + i, low: 9 + i, close: 10.5 + i, volume: 1000,
    }))
    const cache = buildChanlunOverlayCache({
      dates,
      seriesKlines: klines,
      mergedKlines: [
        // A：底分型，前 3 根合并（后两根是被包含的原始K线）
        {
          start: '2024-01-02', end: '2024-01-04', high: 13, low: 9, count: 3, fx: 'bottom',
          included: [
            { date: '2024-01-03', high: 10.8, low: 10.0 },
            { date: '2024-01-04', high: 12.0, low: 10.6 },
          ],
        },
        // B：顶分型，单根（分型框不要求 count>1）
        { start: '2024-01-05', end: '2024-01-05', high: 14, low: 13, count: 1, fx: 'top' },
        // C：既不是分型也没发生合并 → 不画
        { start: '2024-01-06', end: '2024-01-06', high: 15, low: 14, count: 1 },
      ],
      bis: [],
      zhongshus: [],
      signals: [],
      flags: {
        mergedKlines: true, bis: false, xians: false, zhongshus: false,
        signals: false, aiLines: false, supportResistance: false,
      },
    })
    expect(cache.mergedKlines).toHaveLength(3)
    expect(cache.mergedKlines[0]._s).toBe(0)
    expect(cache.mergedKlines[0]._e).toBe(2)
    expect(cache.mergedKlines[1]._s).toBe(3)
    // 被包含K线的日期已在缓存期解析成下标
    expect(cache.mergedKlines[0]._inc.map(x => x.i)).toEqual([1, 2])

    const children = buildChanlunGraphicChildren({
      data: cache,
      viewS: 0,
      viewE: dates.length - 1,
      gridLeft: 58,
      gridRight: 900,
      pixelAtIdx: (i: number, price: number) => [58 + i * 10, 400 - price * 10] as [number, number],
      theme: CHANLUN_OVERLAY_THEME_PC,
    })
    // 只画顶/底分型的合并K线框：A（底分型）+ B（顶分型）；C（无分型）不画。
    // 外加 A 里被包含K线的青色空心框；无 ×N 文字标签。
    const allBoxes = children.filter(c => c.type === 'rect')
    const boxes = allBoxes.filter(
      c => (c.style as { stroke?: string }).stroke !== CHANLUN_OVERLAY_THEME_PC.includedColor,
    )
    const labels = children.filter(c => c.type === 'text')
    expect(boxes).toHaveLength(2)
    expect(allBoxes).toHaveLength(4)
    expect(labels).toHaveLength(0)
    // 颜色按分型（chan.py draw_klc color_type）：底分型→蓝，顶分型→红
    const strokeOf = (c: unknown) => (c as { style: { stroke: string } }).style.stroke
    expect(strokeOf(boxes[0])).toBe(CHANLUN_OVERLAY_THEME_PC.klcBottomColor)
    expect(strokeOf(boxes[1])).toBe(CHANLUN_OVERLAY_THEME_PC.klcTopColor)
    // 回归：无分型的合并区间（哪怕 count>1）不再画普通绿框
    expect(
      children.some(
        c => c.type === 'rect' && strokeOf(c) === CHANLUN_OVERLAY_THEME_PC.klcNormalColor,
      ),
    ).toBe(false)
    // 框的纵向范围对应 high~low：high=13 -> y=270, low=9 -> y=310
    const shapeOf = (c: unknown) =>
      (c as { shape: { x: number; y: number; width: number; height: number } }).shape
    expect(shapeOf(boxes[0]).y).toBe(270)
    expect(shapeOf(boxes[0]).height).toBe(40)
    // 几何：Rectangle((begin_idx - 0.4, low), ...)，柱距 10px → 左右各外扩 4px
    expect(shapeOf(boxes[0]).x).toBeCloseTo(54, 5)      // 58 - 0.4*10
    expect(shapeOf(boxes[0]).width).toBeCloseTo(28, 5)  // (78 + 4) - (58 - 4)
    // 回归防护：ECharts graphic 的 rect 是形状元素，几何必须写在 shape 里。
    // 顶层 width/height 只对 group 的百分比布局有效，写在 rect 上会被忽略 → 0×0 隐形矩形。
    expect((boxes[0] as Record<string, unknown>).width).toBeUndefined()
    expect((boxes[0] as Record<string, unknown>).height).toBeUndefined()
    // 被包含的原始K线用青色空心框标出（2 根），宽度按柱距自适应
    const incBoxes = children.filter(
      c => c.type === 'rect' && (c.style as { stroke?: string }).stroke === CHANLUN_OVERLAY_THEME_PC.includedColor,
    )
    expect(incBoxes).toHaveLength(2)
    // pixelAtIdx 里每根柱间距 10px → 框宽 7.2px，居中在柱中心
    expect(shapeOf(incBoxes[0]).width).toBeCloseTo(7.2, 5)
    expect(shapeOf(incBoxes[0]).x).toBeCloseTo(68 - 3.6, 5)  // 第 1 根：x = 58 + 10
    // 真实高低范围：high=10.8 -> y=292, low=10.0 -> y=300
    expect(shapeOf(incBoxes[0]).y).toBe(292)
    expect(shapeOf(incBoxes[0]).height).toBe(8)
  })

  it('draws bs points as arrows (buy red / sell green) and bi/seg/zs dash by is_sure', () => {
    const dates = ['2024-01-02', '2024-01-03', '2024-01-04']
    const klines = dates.map((d, i) => ({
      date: d, open: 10 + i, high: 11 + i, low: 9 + i, close: 10.5 + i, volume: 1000,
    }))
    const cache = buildChanlunOverlayCache({
      dates,
      seriesKlines: klines,
      bis: [{
        id: 'b1', start: '2024-01-02', end: '2024-01-04', direction: 'up',
        high: 12, low: 9, start_price: 9, end_price: 12, is_sure: false,
      }],
      zhongshus: [{
        id: 'z1', start: '2024-01-02', end: '2024-01-04', range_high: 11, range_low: 10,
        is_sure: false,
      }],
      signals: [{
        type: '一买', level: 'daily', price: 9, datetime: '2024-01-02',
        confidence: 0.8, description: '',
      }],
      flags: {
        mergedKlines: false, bis: true, xians: false, zhongshus: true,
        signals: true, aiLines: false, supportResistance: false,
      },
    })
    const children = buildChanlunGraphicChildren({
      data: cache,
      viewS: 0,
      viewE: dates.length - 1,
      gridLeft: 58,
      gridRight: 900,
      pixelAtIdx: (i: number, price: number) => [58 + i * 10, 400 - price * 10] as [number, number],
      theme: CHANLUN_OVERLAY_THEME_PC,
    })
    // 笔：未确认 → 虚线
    const biLine = children.find(
      c => c.type === 'line' && (c.style as { stroke?: string }).stroke === CHANLUN_OVERLAY_THEME_PC.biColor,
    )
    expect((biLine?.style as { lineDash?: number[] }).lineDash).toEqual([6, 4])
    // 中枢：未确认 → 虚线框
    const zsBox = children.find(c => c.type === 'rect')
    expect((zsBox?.style as { lineDash?: number[] }).lineDash).toEqual([6, 4])
    // 买卖点：买点红色 + 箭头（三角 polygon）+ 文字
    const buyColor = CHANLUN_OVERLAY_THEME_PC.buyColors['一买']
    const polys = children.filter(
      c => c.type === 'polygon' && (c.style as { fill?: string }).fill === buyColor,
    )
    expect(polys).toHaveLength(1)
    const pts = (polys[0].shape as { points: number[][] }).points
    // 箭头尖端指向价格点 price=9 → y=310，买点在下方所以底边在 y=316
    expect(pts[0][1]).toBe(310)
    expect(pts[1][1]).toBe(316)
  })
})
