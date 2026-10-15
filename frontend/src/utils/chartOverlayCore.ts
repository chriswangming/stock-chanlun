/**
 * 缠论 ECharts graphic 叠加层 — PC / 移动端共用（索引映射 + 像素渲染）。
 */
import type {
  KLine, Bi, XianSegment, Zhongshu, Signal, AISignal, SupportResistance, MergedKline,
} from '../api/stock'
import { buildDateLookup, dateToIdxRobust, resolveBarRange } from './chartDateUtils'
import { simplifySupportResistanceLevels } from './chartOverlayUtils'

export type GraphicElement = Record<string, unknown>

export type IndexedRange<T> = T & { _s: number; _e: number }
export type IndexedSignal = Signal & { _idx: number }
/** 合并K线：included 的日期在缓存期就解析成原始K线下标，绘制时不再查表 */
export type IndexedMergedKline = IndexedRange<MergedKline> & {
  _inc: { i: number; high: number; low: number }[]
}

export type ChanlunOverlayPayload = {
  /** 包含处理后的合并K线（已映射到原始K线下标区间） */
  mergedKlines: IndexedMergedKline[]
  bis: IndexedRange<Bi>[]
  xians: IndexedRange<XianSegment>[]
  zhongshus: IndexedRange<Zhongshu>[]
  signals: IndexedSignal[]
  aiSignal: AISignal | null | undefined
  supportResistance: SupportResistance[]
  dualCrossIndices: number[]
  _n: number
}

export type ChanlunOverlayFlags = {
  /** 主图上画合并K线区间框 */
  mergedKlines: boolean
  bis: boolean
  xians: boolean
  zhongshus: boolean
  signals: boolean
  aiLines: boolean
  supportResistance: boolean
}

export type ChanlunOverlayTheme = {
  upColor: string
  downColor: string
  strokeBg: string
  labelFont: string
  signalRadius: number
  signalFontSize: number
  zsLabelFontSize: number
  srLabelFontSize: number
  /** 笔：chan.py draw_bi 默认 color='black' —— 保持深黑，靠 biShadow 描边在深色底上提可见度 */
  biColor: string
  /** 笔的描边色（深色底上给黑线加白色外描边，否则纯黑看不见） */
  biShadow: string
  /** 线段：chan.py draw_seg 默认 color='g' */
  segColor: string
  /** 中枢：chan.py draw_zs 默认 color='orange' */
  zsStroke: string
  zsFill: string
  /** 合并K线框：chan.py draw_klc —— 顶分型红 / 底分型蓝 / 其余绿 */
  klcTopColor: string
  klcBottomColor: string
  klcNormalColor: string
  /** 被包含的原始K线（chan.py 无此绘制，本项目额外要求，用青色以区别于底分型蓝） */
  includedColor: string
  mergedFontSize: number
  aiFontSize: number
  resonanceFontSize: number
  buyColors: Record<string, string>
  sellColors: Record<string, string>
}

// 主图是白底（浏览器实际渲染口径）：所有描边/文字都要用「在白纸上能看清」的深色，
// chan.py 出图本来就是白底黑线，这里直接沿用它的色系，只是把亮色调深一档。
export const CHANLUN_OVERLAY_THEME_PC: ChanlunOverlayTheme = {
  upColor: '#dc2626',
  downColor: '#16a34a',
  strokeBg: '#ffffff',
  labelFont: 'Noto Sans SC',
  signalRadius: 8,
  signalFontSize: 11,
  zsLabelFontSize: 9,
  srLabelFontSize: 9,
  biColor: '#000000',
  // 白底上黑线本身就很清楚，不需要描边
  biShadow: 'transparent',
  segColor: '#16a34a',
  zsStroke: 'rgba(234, 88, 12, 0.9)',
  zsFill: 'rgba(255, 165, 0, 0.15)',
  klcTopColor: '#dc2626',
  klcBottomColor: '#1f6feb',
  klcNormalColor: '#16a34a',
  includedColor: '#0891b2',
  mergedFontSize: 9,
  aiFontSize: 11,
  resonanceFontSize: 9,
  // chan.py draw_bs_point: buy_color='r' / sell_color='g'（白底上加深一档）
  buyColors: { '一买': '#dc2626', '二买': '#ef4444', '三买': '#b91c1c' },
  sellColors: { '一卖': '#16a34a', '二卖': '#15803d', '三卖': '#166534' },
}

export const CHANLUN_OVERLAY_THEME_MOBILE: ChanlunOverlayTheme = {
  upColor: '#dc2626',
  downColor: '#16a34a',
  strokeBg: '#ffffff',
  labelFont: 'monospace',
  signalRadius: 7,
  signalFontSize: 10,
  zsLabelFontSize: 9,
  srLabelFontSize: 8,
  biColor: '#000000',
  biShadow: 'transparent',
  segColor: '#16a34a',
  zsStroke: 'rgba(234, 88, 12, 0.9)',
  zsFill: 'rgba(255, 165, 0, 0.15)',
  klcTopColor: '#dc2626',
  klcBottomColor: '#0284c7',
  klcNormalColor: '#16a34a',
  includedColor: '#0891b2',
  mergedFontSize: 8,
  aiFontSize: 10,
  resonanceFontSize: 8,
  // chan.py draw_bs_point: buy_color='r' / sell_color='g'（白底上加深一档）
  buyColors: { '一买': '#dc2626', '二买': '#ef4444', '三买': '#b91c1c' },
  sellColors: { '一卖': '#16a34a', '二卖': '#15803d', '三卖': '#166534' },
}

type DataZoomSlice = { startValue?: number; endValue?: number; start?: number; end?: number }

export function resolveDataZoomViewRange(
  datesLength: number,
  dataZoom?: DataZoomSlice[],
): { viewS: number; viewE: number } {
  const dz0 = dataZoom?.[0]
  const vStart = dz0?.startValue ?? 0
  const vEnd = dz0?.endValue ?? (datesLength - 1)
  const viewS = Math.max(0, Math.min(Number(vStart) || 0, datesLength - 1))
  const viewE = Math.max(viewS, Math.min(Number(vEnd) || (datesLength - 1), datesLength - 1))
  return { viewS, viewE }
}

export function createCachedPixelFn(
  pixelAtIdx: (i: number, price: number) => [number, number] | null,
): (i: number, price: number) => [number, number] | null {
  const cache = new Map<string, [number, number] | null>()
  return (i: number, price: number) => {
    const key = `${i}:${price}`
    if (cache.has(key)) return cache.get(key) ?? null
    const pt = pixelAtIdx(i, price)
    cache.set(key, pt)
    return pt
  }
}

export function buildChanlunOverlayCache(params: {
  dates: string[]
  seriesKlines: KLine[]
  mergedKlines?: MergedKline[]
  bis: Bi[]
  xians?: XianSegment[]
  zhongshus: Zhongshu[]
  signals: Signal[]
  aiSignal?: AISignal | null
  supportResistance?: SupportResistance[]
  flags: ChanlunOverlayFlags
  dualCrossIndices?: number[]
}): ChanlunOverlayPayload {
  const {
    dates,
    seriesKlines,
    mergedKlines,
    bis,
    xians,
    zhongshus,
    signals,
    aiSignal,
    supportResistance,
    flags,
    dualCrossIndices = [],
  } = params
  const nBar = dates.length
  const refPx = seriesKlines.length > 0 ? seriesKlines[seriesKlines.length - 1].close : 1
  const dateLookup = buildDateLookup(dates)

  return {
    mergedKlines: flags.mergedKlines && mergedKlines ? mergedKlines.flatMap(m => {
      const r = resolveBarRange(m.start, m.end, nBar, dates, dateLookup)
      if (!r) return []
      const inc = (m.included ?? []).flatMap(x => {
        const i = dateToIdxRobust(x.date, dates, dateLookup)
        return i >= 0 ? [{ i, high: x.high, low: x.low }] : []
      })
      return [{ ...m, _s: r[0], _e: r[1], _inc: inc }]
    }) : [],
    bis: flags.bis ? bis.flatMap(b => {
      const r = resolveBarRange(b.start, b.end, nBar, dates, dateLookup)
      return r ? [{ ...b, _s: r[0], _e: r[1] }] : []
    }) : [],
    xians: flags.xians && xians ? xians.flatMap(x => {
      const r = resolveBarRange(x.start, x.end, nBar, dates, dateLookup)
      return r ? [{ ...x, _s: r[0], _e: r[1] }] : []
    }) : [],
    zhongshus: flags.zhongshus ? zhongshus.flatMap(z => {
      const r = resolveBarRange(z.start, z.end, nBar, dates, dateLookup)
      return r ? [{ ...z, _s: r[0], _e: r[1] }] : []
    }) : [],
    signals: flags.signals ? signals.flatMap(s => {
      const ix = dateToIdxRobust(s.datetime, dates, dateLookup)
      return ix >= 0 ? [{ ...s, _idx: ix }] : []
    }) : [],
    aiSignal: flags.aiLines ? (aiSignal ?? null) : null,
    supportResistance: flags.supportResistance
      ? simplifySupportResistanceLevels(supportResistance || [], refPx)
      : [],
    dualCrossIndices,
    _n: nBar,
  }
}

export function buildChanlunGraphicChildren(ctx: {
  data: ChanlunOverlayPayload
  viewS: number
  viewE: number
  gridLeft: number
  gridRight: number
  pixelAtIdx: (i: number, price: number) => [number, number] | null
  theme: ChanlunOverlayTheme
  /** 共振标记取价；缺省用 pixelAtIdx(i, 0) 的 y（由调用方传入 close） */
  priceAtIdx?: (i: number) => number
}): GraphicElement[] {
  const { data, viewS, viewE, gridLeft, gridRight, theme } = ctx
  const pixelAtIdxCached = createCachedPixelFn(ctx.pixelAtIdx)
  const priceAt = ctx.priceAtIdx ?? (() => 0)
  const children: GraphicElement[] = []

  const {
    bis, xians, zhongshus, signals, aiSignal, supportResistance, dualCrossIndices,
    mergedKlines,
  } = data

  // 一根K线占多少像素（相邻索引的 x 间距）；缩到看不见单根时也不会是 0
  const pixelStepAt = (i: number): number => {
    const cur = pixelAtIdxCached(i, 0)?.[0]
    if (cur == null) return 4
    const nxt = pixelAtIdxCached(i + 1, 0)?.[0]
    if (nxt != null && nxt > cur) return Math.max(nxt - cur, 2)
    const prv = pixelAtIdxCached(i - 1, 0)?.[0]
    if (prv != null && cur > prv) return Math.max(cur - prv, 2)
    return 4
  }

  // 合并K线 —— 对齐 chan.py CPlotDriver.draw_klc 的配色：
  //   color_type = {TOP: 'red', BOTTOM: 'blue', UP: 'green', DOWN: 'green'}
  //   几何 Rectangle((begin_idx - width, low), end_idx - begin_idx + width*2, high - low)，width=0.4
  //   fill=False（空心）。
  //   绘制范围（本项目口径）：只画顶/底分型所在的合并K线框。
  //   普通合并区间（fx 为空）和单根K线都不画——框住它们没有额外信息量。
  //   注意：分型框不要求 count>1，实测约 2/3 的分型合并K线本身就是单根，
  //   若再卡 count 会把大部分顶/底分型都漏掉。
  const KLC_WIDTH = 0.4
  const mkInView = (mergedKlines ?? []).filter(
    m => (m.fx === 'top' || m.fx === 'bottom')
      && !(m._e < viewS || m._s > viewE || m._e < m._s),
  )
  // 保护：视窗内矩形过多时跳过绘制，避免 graphic 元素爆炸拖垮渲染
  // （1000 根 K 线约能合并出 700 根，上限要留足，否则整组被静默跳过）
  if (mkInView.length <= 900) {
    for (const m of mkInView) {
      const a = pixelAtIdxCached(m._s, m.high)
      const b = pixelAtIdxCached(m._e, m.low)
      if (!a || !b) continue
      const pad = KLC_WIDTH * pixelStepAt(m._s)
      const x1 = a[0] - pad
      const x2 = b[0] + pad
      const y1 = Math.min(a[1], b[1])
      const y2 = Math.max(a[1], b[1])
      const klcColor = m.fx === 'top'
        ? theme.klcTopColor
        : m.fx === 'bottom'
          ? theme.klcBottomColor
          : theme.klcNormalColor
      // 注意：ECharts graphic 的 rect 是「形状元素」，几何必须写在 shape 里。
      // 顶层 width/height 只对 group 的百分比布局有效，写在 rect 上会被忽略，
      // 结果就是 0×0 的隐形矩形（合并K线/中枢此前画不出来的根因）。
      children.push({
        type: 'rect',
        shape: {
          x: x1,
          y: y1,
          // 缩到很密时（柱距 < 2px）框会被压成一条线，给个最小宽度保证还看得见
          width: Math.max(x2 - x1, 3),
          height: Math.max(y2 - y1, 1),
        },
        style: { fill: 'transparent', stroke: klcColor, lineWidth: 1.2 },
        z: 97, silent: true,
      })
      // 被包含的原始K线（本项目额外要求，chan.py 不画）：青色空心框标出真实高低范围
      for (const x of m._inc) {
        if (x.i < viewS || x.i > viewE) continue
        const p1 = pixelAtIdxCached(x.i, x.high)
        const p2 = pixelAtIdxCached(x.i, x.low)
        if (!p1 || !p2) continue
        const cx = p1[0]
        const top = Math.min(p1[1], p2[1])
        const bottom = Math.max(p1[1], p2[1])
        const bw = pixelStepAt(x.i) * 0.72
        children.push({
          type: 'rect',
          shape: {
            x: cx - bw / 2,
            y: top,
            width: Math.max(bw, 2.5),
            height: Math.max(bottom - top, 1),
          },
          style: { fill: 'transparent', stroke: theme.includedColor, lineWidth: 1.1 },
          z: 96, silent: true,
        })
      }
    }
  }

  for (const zs of zhongshus) {
    if (zs._e < viewS || zs._s > viewE || zs._e < zs._s) continue
    const a = pixelAtIdxCached(zs._s, zs.range_high)
    const b = pixelAtIdxCached(zs._e, zs.range_low)
    if (!a || !b) continue
    const xPx1 = Math.min(a[0], b[0])
    const xPx2 = Math.max(a[0], b[0])
    const yPx1 = Math.min(a[1], b[1])
    const yPx2 = Math.max(a[1], b[1])
    children.push({
      type: 'rect',
      // 几何必须放 shape（顶层 width/height 对 rect 无效，会导致 0×0 不可见）
      shape: {
        x: xPx1,
        y: yPx1,
        width: Math.max(xPx2 - xPx1, 4),
        height: Math.max(yPx2 - yPx1, 1),
      },
      style: {
        fill: theme.zsFill,
        stroke: theme.zsStroke,
        lineWidth: 2,
        // chan.py draw_zs: line_style = '-' if is_sure else '--'
        lineDash: zs.is_sure === false ? [6, 4] : undefined,
      },
      z: 100,
      silent: true,
    })
  }

  // 笔 —— 对齐 chan.py draw_bi / plot_bi_element：
  //   color='black'（深黑）；is_sure 实线，否则虚线；不画端点装饰。
  //   深黑在深色底上几乎不可见，故加白色外描边（shadow）让它浮出来。
  for (const bi of bis) {
    if (bi._e < viewS || bi._s > viewE || bi._e < bi._s) continue
    const p1 = pixelAtIdxCached(bi._s, bi.start_price ?? (bi.direction === 'up' ? bi.low : bi.high))
    const p2 = pixelAtIdxCached(bi._e, bi.end_price ?? (bi.direction === 'up' ? bi.high : bi.low))
    if (!p1 || !p2) continue
    children.push({
      type: 'line',
      shape: { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] },
      style: {
        stroke: theme.biColor,
        lineWidth: 1.8,
        lineDash: bi.is_sure === false ? [6, 4] : undefined,
        shadowColor: theme.biShadow,
        shadowBlur: 4,
      },
      z: 102, silent: true,
    })
  }

  // 线段 —— 对齐 chan.py draw_seg：color='g'、linewidth 粗；is_sure 实线，否则虚线
  for (const xiang of xians) {
    if (xiang._e < viewS || xiang._s > viewE || xiang._e < xiang._s) continue
    const p1 = pixelAtIdxCached(
      xiang._s,
      xiang.start_price ?? (xiang.direction === 'up' ? xiang.low : xiang.high),
    )
    const p2 = pixelAtIdxCached(
      xiang._e,
      xiang.end_price ?? (xiang.direction === 'up' ? xiang.high : xiang.low),
    )
    if (!p1 || !p2) continue
    children.push({
      type: 'line',
      shape: { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] },
      style: {
        stroke: theme.segColor,
        lineWidth: 3,
        lineDash: xiang.is_sure === false ? [10, 6] : undefined,
        opacity: 0.95,
      },
      z: 101, silent: true,
    })
  }

  // 买卖点 —— 对齐 chan.py draw_bs_point / bsp_common_draw：
  //   buy_color='r' / sell_color='g'；y = low(买) / high(卖)；
  //   箭头从价格点外侧指回价格（买点箭头在下方朝上，卖点在上方朝下），文字跟在箭头尾部。
  //   chan.py 的 arrow_l=0.15*y_range，这里用固定像素，缩放时更稳定。
  const BSP_ARROW_LEN = 26
  const BSP_ARROW_HEAD = 6
  for (const sig of signals) {
    if (sig._idx < viewS || sig._idx > viewE) continue
    const pt = pixelAtIdxCached(sig._idx, sig.price)
    if (!pt) continue
    const color = theme.buyColors[sig.type] || theme.sellColors[sig.type] || '#d29922'
    const isBuy = sig.type.includes('买')
    // 屏幕坐标 y 向下为正：买点箭头画在价格下方(+1)，卖点画在上方(-1)
    const dir = isBuy ? 1 : -1
    const ax = pt[0]
    const tailY = pt[1] + dir * BSP_ARROW_LEN
    const headY = pt[1] + dir * BSP_ARROW_HEAD
    children.push({
      type: 'line',
      shape: { x1: ax, y1: tailY, x2: ax, y2: headY },
      style: { stroke: color, lineWidth: 1.4 },
      z: 103, silent: true,
    })
    children.push({
      type: 'polygon',
      shape: { points: [[ax, pt[1]], [ax - 4, headY], [ax + 4, headY]] },
      style: { fill: color },
      z: 103, silent: true,
    })
    children.push({
      type: 'text',
      style: {
        text: sig.type,
        fill: color,
        fontSize: theme.signalFontSize,
        fontWeight: 700,
        fontFamily: theme.labelFont,
        textAlign: 'center',
      },
      x: ax,
      y: isBuy ? tailY + 3 : tailY - theme.signalFontSize - 3,
      z: 104, silent: true,
    })
  }

  for (const lvl of supportResistance) {
    const yp = pixelAtIdxCached(viewS, lvl.price)?.[1]
    if (yp == null || !Number.isFinite(yp)) continue
    const isSupport = lvl.type === 'support'
    const color = isSupport ? theme.downColor : theme.upColor
    const dash = isSupport ? [6, 4] : [8, 4]
    const label = isSupport ? `撑 ${lvl.price.toFixed(2)}` : `阻 ${lvl.price.toFixed(2)}`
    const strength = lvl.strength ?? 0.5
    const lw = 0.55 + strength * 0.45
    children.push({
      type: 'line',
      shape: { x1: gridLeft, y1: yp, x2: gridRight, y2: yp },
      style: { stroke: color, lineWidth: lw, opacity: 0.22 + strength * 0.28, lineDash: dash },
      z: 98, silent: true,
    })
    children.push({
      type: 'text',
      style: {
        text: label,
        fill: color,
        fontSize: theme.srLabelFontSize,
        fontFamily: theme.labelFont,
        opacity: 0.45 + strength * 0.35,
      },
      x: gridLeft + 6, y: yp - 4,
      z: 99, silent: true,
    })
  }

  if (aiSignal) {
    const entryColor = aiSignal.direction === '买入' ? theme.downColor : theme.upColor
    const hLine = (price: number, stroke: string, dash: number[], lw: number, label?: string, labelYOffset = -6) => {
      const yp = pixelAtIdxCached(viewS, price)?.[1]
      if (yp == null || !Number.isFinite(yp)) return
      children.push({
        type: 'line',
        shape: { x1: gridLeft, y1: yp, x2: gridRight, y2: yp },
        style: { stroke, lineWidth: lw, opacity: 0.85, lineDash: dash },
        z: 99, silent: true,
      })
      if (label) {
        children.push({
          type: 'text',
          style: { text: label, fill: stroke, fontSize: theme.aiFontSize, fontWeight: 700, fontFamily: theme.labelFont },
          x: gridLeft + 8, y: yp + labelYOffset,
          z: 104, silent: true,
        })
      }
    }
    if (aiSignal.entry_price != null) {
      hLine(aiSignal.entry_price, entryColor, [7, 4], 1.5, `入场 ${aiSignal.entry_price.toFixed(2)}`, -6)
    }
    if (aiSignal.stop_loss != null) {
      hLine(aiSignal.stop_loss, theme.upColor, [3, 3], 1.2, `止损 ${aiSignal.stop_loss.toFixed(2)}`, 14)
    }
    if (aiSignal.take_profit != null) {
      hLine(aiSignal.take_profit, theme.downColor, [3, 3], 1.2, `止盈 ${aiSignal.take_profit.toFixed(2)}`, -6)
    }
  }

  for (const idx of dualCrossIndices) {
    if (idx < viewS || idx > viewE) continue
    const pt = pixelAtIdxCached(idx, priceAt(idx))
    if (!pt) continue
    children.push({
      type: 'circle',
      shape: { cx: pt[0], cy: pt[1], r: 5 },
      style: { fill: 'rgba(245,158,11,0.9)', stroke: theme.strokeBg, lineWidth: 1.5 },
      z: 106, silent: true,
    })
    children.push({
      type: 'text',
      style: {
        text: '共振',
        fill: '#b45309',
        fontSize: theme.resonanceFontSize,
        fontWeight: 600,
        fontFamily: theme.labelFont,
        textAlign: 'center',
      },
      x: pt[0], y: pt[1] - 10,
      z: 107, silent: true,
    })
  }

  return children
}
