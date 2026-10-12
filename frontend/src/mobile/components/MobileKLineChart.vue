<template>
  <div class="kline-wrap">
    <!-- 主图 + 副图 -->
    <div
      ref="chartRef"
      class="kline-chart"
      :class="{ 'chart-ready': !loading }"
      @touchstart="onTouchStart"
      @touchmove="onTouchMove"
      @touchend="onTouchEnd"
    />

    <!-- 底部状态栏 -->
    <div v-if="barInfoText" class="bar-info">{{ barInfoText }}</div>

    <!-- 骨架屏（数据加载中） -->
    <div v-if="loading" class="chart-skeleton" aria-label="K线加载中">
      <div class="skel-header">
        <div class="skel-bar short" />
        <div class="skel-bar medium" />
        <div class="skel-bar short" />
      </div>
      <div class="skel-chart-area">
        <div class="skel-candle" style="height:65%;left:4%" />
        <div class="skel-candle" style="height:48%;left:10%" />
        <div class="skel-candle" style="height:72%;left:16%" />
        <div class="skel-candle" style="height:55%;left:22%" />
        <div class="skel-candle" style="height:80%;left:28%" />
        <div class="skel-candle" style="height:60%;left:34%" />
        <div class="skel-candle" style="height:70%;left:40%" />
        <div class="skel-candle" style="height:45%;left:46%" />
        <div class="skel-candle" style="height:75%;left:52%" />
        <div class="skel-candle" style="height:58%;left:58%" />
        <div class="skel-candle" style="height:68%;left:64%" />
        <div class="skel-candle" style="height:52%;left:70%" />
        <div class="skel-candle" style="height:78%;left:76%" />
        <div class="skel-candle" style="height:62%;left:82%" />
        <div class="skel-candle" style="height:55%;left:88%" />
      </div>
      <div class="skel-ma-row">
        <div class="skel-dot ma5" />
        <div class="skel-dot ma20" />
        <div class="skel-dot ma60" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, nextTick, computed } from 'vue'
import echarts from '../../utils/echarts'
import type {
  KLine, Bi, XianSegment, Zhongshu, Signal, AISignal, SupportResistance, MergedKline,
} from '@/api/stock'
import type { IndicatorConfig } from '@/stores/chanlun'
import { calcMA, computeDualMacdSkdjMarkerIndices } from '@/utils/stockIndicators'
import { downsampleKlines, klineSeriesSignature } from '@/utils/chartDownsample'
import { setChartOptionKeepDataZoom } from '@/utils/chartEchartsHelpers'
import { formatAxisLabel, formatStamp, normTs } from '@/utils/chartDateUtils'
import { useDebouncedCallback } from '@/composables/useDebounce'
import { useKlineIndicators } from '@/composables/useKlineIndicators'
import {
  type ChanlunOverlayPayload,
  CHANLUN_OVERLAY_THEME_MOBILE,
  buildChanlunGraphicChildren,
  buildChanlunOverlayCache,
  resolveDataZoomViewRange,
} from '@/utils/chartOverlayCore'

const LONG_PRESS_DELAY = 400
const SWIPE_THRESHOLD = 50

const props = defineProps<{
  klines: KLine[]
  /** 包含处理后的合并K线（chan.py CKLine），在主图上画区间框 */
  mergedKlines?: MergedKline[]
  bis: Bi[]
  zhongshus: Zhongshu[]
  signals: Signal[]
  xians?: XianSegment[]
  aiSignal?: AISignal | null
  supportResistance?: SupportResistance[]
  indicators?: IndicatorConfig
  zoomStart?: number
  zoomEnd?: number
  loading?: boolean
}>()

const emit = defineEmits<{ 'zoomChange': [start: number, end: number] }>()

const chartRef = ref<HTMLDivElement | null>(null)
const barInfoText = ref('')
let chart: echarts.ECharts | null = null

const displayKlines = computed(() => downsampleKlines(props.klines))
const klineIndicators = useKlineIndicators(displayKlines)

let graphicRaf = 0
const PANEL_SEP_COLOR = '#d8dee4'
let resizeObserver: ResizeObserver | null = null
let longPressTimer: ReturnType<typeof setTimeout> | null = null
let touchStartX = 0
let touchStartY = 0
let lastTouchX = 0
let isScrolling = false
let touchCount = 0

type DataZoomOption = { startValue?: number; endValue?: number; start?: number; end?: number }

// ── 指标配置 ────────────────────────────────────────────────────────────────
function getIndicators(): Required<IndicatorConfig> {
  return {
    ma5: true, ma20: true, ma60: true,
    bis: true, xians: false, zhongshus: true,
    signals: true, aiLines: true, supportResistance: true, mergedKlines: true,
    volume: true, macd: false, rsi: false, skdj: false,
    ...(props.indicators || {})
  }
}

// ── 缓存 ────────────────────────────────────────────────────────────────────
let lastDates: string[] = []
let lastDisplayKlines: KLine[] = []
let lastMa5: (number | null)[] = []
let lastMa20: (number | null)[] = []
let lastMa60: (number | null)[] = []
// tooltip 的副图数值要在 buildOption 之外取，这里缓存最近一次的指标与开关
let lastIndicatorConfig: Required<IndicatorConfig> | null = null
let lastMacd: { dif: number[]; dea: number[] } | null = null
let lastRsi: (number | null)[] | null = null
let lastSkdj: { sk: (number | null)[]; sd: (number | null)[] } | null = null

function fmtPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(2)
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

function fmtVol(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const n = Math.abs(v)
  if (n >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (n >= 1e4) return `${(v / 1e4).toFixed(2)}万`
  return String(Math.round(v))
}

function formatBarLine(idx: number): string {
  if (idx < 0 || idx >= lastDisplayKlines.length) return ''
  const k = lastDisplayKlines[idx]
  const d = formatStamp(lastDates[idx] ?? k.date)
  return `${d}  开 ${fmtPrice(k.open)}  收 ${fmtPrice(k.close)}  高 ${fmtPrice(k.high)}  低 ${fmtPrice(k.low)}  |  MA5 ${fmtPrice(lastMa5[idx])}  MA20 ${fmtPrice(lastMa20[idx])}  MA60 ${fmtPrice(lastMa60[idx])}`
}

function setBarInfoByIndex(idx: number) {
  if (!lastDisplayKlines.length) { barInfoText.value = ''; return }
  const i = Math.max(0, Math.min(idx, lastDisplayKlines.length - 1))
  barInfoText.value = formatBarLine(i)
}

// ── 颜色常量 ────────────────────────────────────────────────────────────────
// 涨红跌绿（A股/港股口径）。白底上压深一档，否则浅绿阴线块看不清。
const UP_COLOR = '#dc2626'
const DOWN_COLOR = '#16a34a'
// 浏览器里主图渲染出来是白底：网格/文字都按浅色底取值
const GRID_COLOR = 'rgba(0,0,0,0.08)'
const TEXT_COLOR = '#57606a'

// ── pixelAt ─────────────────────────────────────────────────────────────────
function pixelAtIdx(i: number, price: number): [number, number] | null {
  if (i < 0 || i >= lastDates.length || !chart) return null
  try {
    const pt = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [lastDates[i], price]) as unknown
    if (!Array.isArray(pt) || pt.length < 2) return null
    const x = Number(pt[0]), y = Number(pt[1])
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null
  } catch { return null }
}

function getGridBounds(): [number, number] {
  if (!chart || !lastDates.length) return [0, 400]
  const mid = props.klines[Math.floor(props.klines.length / 2)]?.close ?? 1
  try {
    const p0 = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [lastDates[0], mid]) as number[]
    const p1 = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [lastDates[lastDates.length - 1], mid]) as number[]
    if (p0?.[0] != null && p1?.[0] != null) {
      return [Math.min(p0[0], p1[0]), Math.max(p0[0], p1[0])]
    }
  } catch { /* ignore */ }
  return [0, 400]
}

// ── 缠论 Overlay ─────────────────────────────────────────────────────────────
let overlayCache: ChanlunOverlayPayload | null = null

function buildOverlayData(): ChanlunOverlayPayload {
  const ind = getIndicators()
  const indData = klineIndicators.value
  const chartKlines = lastDisplayKlines

  let dualCrossIndices: number[] = []
  if (chartKlines.length >= 30 && ind.macd && ind.skdj && indData.macd && indData.sk && indData.sd) {
    dualCrossIndices = computeDualMacdSkdjMarkerIndices(chartKlines, 3, 1e-9, {
      dif: indData.macd.dif,
      dea: indData.macd.dea,
      sk: indData.sk,
      sd: indData.sd,
    }).indices
  }

  return buildChanlunOverlayCache({
    dates: lastDates,
    seriesKlines: chartKlines,
    mergedKlines: props.mergedKlines,
    bis: props.bis,
    xians: props.xians,
    zhongshus: props.zhongshus,
    signals: props.signals,
    aiSignal: props.aiSignal,
    supportResistance: props.supportResistance,
    flags: {
      mergedKlines: ind.mergedKlines,
      bis: ind.bis,
      xians: ind.xians,
      zhongshus: ind.zhongshus,
      signals: ind.signals,
      aiLines: ind.aiLines,
      supportResistance: ind.supportResistance,
    },
    dualCrossIndices,
  })
}

/** 主图/副图分隔线的 y 坐标（buildOption 里算，buildPanelSeparators 里用） */
let panelSepTops: number[] = []

/**
 * 面板分隔线。缠论叠加层用 replaceMerge:['graphic'] 整组替换 graphic，
 * 所以每次写 graphic 都要把分隔线一起带上，否则会被擦掉。
 */
function buildPanelSeparators(): Record<string, unknown>[] {
  const w = chart?.getWidth?.() ?? 0
  if (!w || !panelSepTops.length) return []
  return panelSepTops.map((y, i) => ({
    id: `panel-sep-${i}`,
    type: 'line',
    silent: true,
    z: 50,
    shape: { x1: 0, y1: y, x2: w, y2: y },
    style: { stroke: PANEL_SEP_COLOR, lineWidth: 1 },
  }))
}

function applyGraphicOverlay() {
  if (!chart) return
  const data = overlayCache
  if (!data || data._n <= 0 || !lastDates.length) {
    chart.setOption({ graphic: buildPanelSeparators() }, { replaceMerge: ['graphic'] })
    return
  }

  const opt = chart.getOption() as { dataZoom?: DataZoomOption[] }
  const { viewS, viewE } = resolveDataZoomViewRange(lastDates.length, opt?.dataZoom)
  const [gridLeft, gridRight] = getGridBounds()

  const children = buildChanlunGraphicChildren({
    data,
    viewS,
    viewE,
    gridLeft,
    gridRight,
    pixelAtIdx,
    theme: CHANLUN_OVERLAY_THEME_MOBILE,
    priceAtIdx: i => lastDisplayKlines[i]?.close ?? 0,
  })

  chart.setOption({
    graphic: [
      ...buildPanelSeparators(),
      { id: 'chanlun-overlay', type: 'group', children, z: 100, silent: true },
    ]
  }, { replaceMerge: ['graphic'] })
}

function queueGraphic() {
  if (!chart) return
  cancelAnimationFrame(graphicRaf)
  graphicRaf = requestAnimationFrame(() => {
    graphicRaf = 0
    applyGraphicOverlay()
  })
}

// ── 构建 ECharts Option ───────────────────────────────────────────────────────
function buildOption(chartH: number = 300) {
  const klines = displayKlines.value
  if (!klines.length) return {}

  lastDisplayKlines = klines
  // 分钟级周期同一天有多根 bar，类目必须用完整时间戳才唯一
  const dates = klines.map(k => normTs(k.date))
  const ohlc = klines.map(k => [k.open, k.close, k.low, k.high])
  const closes = klines.map(k => k.close)
  const ind = getIndicators()
  const indData = klineIndicators.value
  const zoomStart = props.zoomStart ?? 0
  const zoomEnd = props.zoomEnd ?? 100

  lastDates = dates
  lastMa5 = ind.ma5 ? calcMA(closes, 5) : []
  lastMa20 = ind.ma20 ? calcMA(closes, 20) : []
  lastMa60 = ind.ma60 ? calcMA(closes, 60) : []
  lastIndicatorConfig = ind
  lastMacd = indData.macd ?? null
  lastRsi = indData.rsi ?? null
  lastSkdj = indData.sk && indData.sd ? { sk: indData.sk, sd: indData.sd } : null

  const macdData = indData.macd
  const skdjData = indData.sk && indData.sd ? { sk: indData.sk, sd: indData.sd } : null
  const rsiData = indData.rsi

  // 副图开关
  const subCount = [ind.volume, ind.macd, ind.rsi, ind.skdj].filter(Boolean).length
  const chartHeight = chartH
  const gap = 10   // 面板间留白，分隔线画在正中
  const subHeight = subCount > 0 ? ((chartHeight * 0.6 - gap) / subCount) : 0
  const mainH = subCount > 0 ? chartHeight * 0.38 : chartHeight

  // grid index: 0 = main, 1..N = sub-charts
  // 纵轴标签画在 grid 左边界外面，左边距太小会被裁掉（left:8 时标签左边缘在 x<0）
  const AXIS_LEFT = 46
  const AXIS_RIGHT = 10
  const grids: Record<string, unknown>[] = []
  panelSepTops = []
  const xAxes: Record<string, unknown>[] = []
  const yAxes: Record<string, unknown>[] = []
  const seriesList: Record<string, unknown>[] = []

  // Main grid
  grids.push({ left: AXIS_LEFT, right: AXIS_RIGHT, top: 8, bottom: subCount > 0 ? subCount * subHeight + gap * subCount + 4 : 16 })

  // K线（对齐 chan.py draw_klu(rugd=True)：阳线空心、阴线实心）
  seriesList.push({
    name: 'K线', type: 'candlestick', data: ohlc,
    xAxisIndex: 0, yAxisIndex: 0, z: 3,
    itemStyle: {
      color: 'transparent', color0: DOWN_COLOR,
      borderColor: UP_COLOR, borderColor0: DOWN_COLOR,
      borderWidth: 1,
    }
  })

  // 均线
  if (ind.ma5) seriesList.push({ name: 'MA5', type: 'line', data: lastMa5, xAxisIndex: 0, yAxisIndex: 0,
    lineStyle: { width: 1, color: '#d97706' }, symbol: 'none', smooth: false, connectNulls: true, z: 4 })
  if (ind.ma20) seriesList.push({ name: 'MA20', type: 'line', data: lastMa20, xAxisIndex: 0, yAxisIndex: 0,
    lineStyle: { width: 1, color: '#0284c7' }, symbol: 'none', smooth: false, connectNulls: true, z: 4 })
  if (ind.ma60) seriesList.push({ name: 'MA60', type: 'line', data: lastMa60, xAxisIndex: 0, yAxisIndex: 0,
    lineStyle: { width: 1, color: '#7c3aed' }, symbol: 'none', smooth: false, connectNulls: true, z: 4 })

  // Main xAxis（时间只在 tooltip 里显示，坐标轴上的十字线标签不显示）
  xAxes.push({ type: 'category', data: dates, gridIndex: 0, boundaryGap: true,
    axisLine: { lineStyle: { color: GRID_COLOR } },
    axisTick: { show: false },
    axisLabel: { color: TEXT_COLOR, fontSize: 9, show: false },
    splitLine: { show: false },
    axisPointer: { label: { show: false } } })

  // Main yAxis
  yAxes.push({ scale: true, gridIndex: 0,
    splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)', type: 'dashed' } },
    axisLabel: { color: TEXT_COLOR, fontSize: 9 },
    axisLine: { show: false }, axisTick: { show: false } })

  // 副图：成交量 / MACD / RSI / SKDJ
  let subIdx = 1
  const subDef = [
    { key: 'volume' as const, label: '成交量', color: '#7d8590', calc: (i: number) => ({
      value: klines[i].volume,
      itemStyle: { color: klines[i].close >= klines[i].open ? UP_COLOR + '66' : DOWN_COLOR + '66' }
    }) },
    { key: 'macd' as const, label: 'MACD', color: '#0284c7', calc: (i: number) => ({
      value: macdData!.dif[i],
      itemStyle: { color: macdData!.dif[i] >= 0 ? UP_COLOR : DOWN_COLOR }
    }) },
    { key: 'rsi' as const, label: 'RSI', color: '#d97706', calc: (i: number) => ({
      value: rsiData![i] ?? null,
      itemStyle: {}
    }) },
    { key: 'skdj' as const, label: 'SKDJ', color: '#7c3aed', calc: (i: number) => ({
      value: skdjData!.sk[i] ?? null,
      itemStyle: {}
    }) },
  ]

  for (const def of subDef) {
    if (!ind[def.key]) continue
    if (def.key === 'macd' && !macdData) continue
    if (def.key === 'rsi' && !rsiData) continue
    if (def.key === 'skdj' && !skdjData) continue
    const bottom = subCount * subHeight + (subIdx - 1) * (subHeight + gap) + 4
    const subTop = mainH + (subIdx - 1) * (subHeight + gap) + gap
    panelSepTops.push(subTop - gap / 2)
    grids.push({ left: AXIS_LEFT, right: AXIS_RIGHT, top: subTop, height: subHeight, bottom: undefined })

    xAxes.push({ type: 'category', data: dates, gridIndex: subIdx, boundaryGap: true,
      axisLine: { lineStyle: { color: GRID_COLOR } },
      axisTick: { show: false },
      axisLabel: {
        color: TEXT_COLOR, fontSize: 9, show: subIdx === grids.length - 1,
        formatter: (v: string) => formatAxisLabel(v),
      },
      splitLine: { show: false },
      // 副图只保留竖线，不显示时间
      axisPointer: { label: { show: false } } })

    yAxes.push({ scale: true, gridIndex: subIdx,
      splitLine: { show: false },
      // 成交量原始值可达 8 位，纵轴放不下，缩写成万/亿
      axisLabel: {
        color: TEXT_COLOR,
        fontSize: 9,
        formatter: (v: number) => {
          if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(1)}亿`
          if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}万`
          return String(Math.round(v))
        },
      },
      axisLine: { show: false }, axisTick: { show: false },
      max: def.key === 'volume' || def.key === 'macd' ? undefined : 100,
      min: def.key === 'rsi' || def.key === 'skdj' ? 0 : undefined })

    if (def.key === 'volume') {
      seriesList.push({
        name: '成交量', type: 'bar',
        data: klines.map((_, i) => def.calc(i)),
        xAxisIndex: subIdx, yAxisIndex: subIdx,
        barMaxWidth: 6,
      })
    } else if (def.key === 'macd') {
      const macd = macdData!
      seriesList.push({ name: 'MACD', type: 'bar', data: klines.map((_, i) => def.calc(i)),
        xAxisIndex: subIdx, yAxisIndex: subIdx, barMaxWidth: 4 })
      seriesList.push({ name: 'DIF', type: 'line', data: macd.dif,
        xAxisIndex: subIdx, yAxisIndex: subIdx,
        lineStyle: { width: 1, color: '#0284c7' }, symbol: 'none', smooth: true, z: 5 })
      seriesList.push({ name: 'DEA', type: 'line', data: macd.dea,
        xAxisIndex: subIdx, yAxisIndex: subIdx,
        lineStyle: { width: 1, color: '#d97706' }, symbol: 'none', smooth: true, z: 5 })
    } else if (def.key === 'rsi') {
      seriesList.push({ name: 'RSI', type: 'line', data: rsiData,
        xAxisIndex: subIdx, yAxisIndex: subIdx,
        lineStyle: { width: 1.2, color: def.color }, symbol: 'none', smooth: true, z: 5 })
    } else {
      const skdj = skdjData!
      seriesList.push({ name: 'K', type: 'line', data: skdj.sk,
        xAxisIndex: subIdx, yAxisIndex: subIdx,
        lineStyle: { width: 1.2, color: def.color }, symbol: 'none', smooth: true, z: 5 })
      seriesList.push({ name: 'D', type: 'line', data: skdj.sd,
        xAxisIndex: subIdx, yAxisIndex: subIdx,
        lineStyle: { width: 1, color: '#d97706', type: 'dashed' }, symbol: 'none', smooth: true, z: 5 })
    }

    subIdx++
  }

  return {
    // 与浏览器实际渲染一致：图表区白底
    backgroundColor: '#ffffff',
    animation: false,
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    graphic: buildPanelSeparators(),
    dataZoom: [
      { type: 'inside', xAxisIndex: Array.from({ length: grids.length }, (_, i) => i), start: zoomStart, end: zoomEnd },
    ],
    series: seriesList,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross',
        lineStyle: { color: '#8c959f' },
        label: { show: true, backgroundColor: '#1a222d', color: '#f0f4f8', fontSize: 11,
          formatter: (p: { axisDimension?: string; value?: unknown }) =>
            p.axisDimension === 'y' && p.value != null ? Number(p.value).toFixed(2) : (p.value != null ? String(p.value) : '')
        } },
      backgroundColor: '#1a222d', borderColor: 'rgba(255,255,255,0.12)',
      textStyle: { color: '#f0f4f8', fontSize: 11 },
      formatter(params: unknown) {
        const arr = Array.isArray(params) ? params : [params]
        if (!arr.length) return ''
        const first = arr[0] as { dataIndex?: number }
        const idx = first?.dataIndex
        if (idx == null || idx < 0 || idx >= klines.length) return ''
        const k = klines[idx]
        const d = dates[idx]
        const pct = ((k.close - k.open) / k.open * 100)
        const pctStr = pct >= 0 ? '+' : ''
        return `<div style="font-family:monospace;font-size:11px;line-height:1.6">
          <div style="color:${TEXT_COLOR};margin-bottom:4px;border-bottom:1px solid rgba(255,255,255,0.1);padding-bottom:3px">${d}</div>
          <div>开 <b>${fmtPrice(k.open)}</b>  收 <b style="color:${k.close >= k.open ? UP_COLOR : DOWN_COLOR}">${fmtPrice(k.close)}</b></div>
          <div>高 ${fmtPrice(k.high)}  低 ${fmtPrice(k.low)}</div>
          <div style="color:${k.close >= k.open ? UP_COLOR : DOWN_COLOR}">${pctStr}${pct.toFixed(2)}%</div>
          ${ind.ma5 ? `<div><span style="color:#f0b429">MA5</span> ${fmtPrice(lastMa5[idx])}</div>` : ''}
          ${ind.ma20 ? `<div><span style="color:#38bdf8">MA20</span> ${fmtPrice(lastMa20[idx])}</div>` : ''}
          ${ind.ma60 ? `<div><span style="color:#a78bfa">MA60</span> ${fmtPrice(lastMa60[idx])}</div>` : ''}
          ${subIndicatorLines(idx)}
        </div>`
      },
    },
    // 联动所有面板的 x 轴：竖线贯穿主图与全部副图
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
  }
}

/** 副图指标在该 bar 上的数值（索引与 dates 对齐） */
function subIndicatorLines(idx: number): string {
  const ind = lastIndicatorConfig
  if (!ind) return ''
  const out: string[] = []
  if (ind.volume) {
    out.push(`<div><span style="color:#7d8590">成交量</span> ${fmtVol(lastDisplayKlines[idx]?.volume)}</div>`)
  }
  if (ind.macd && lastMacd) {
    const dif = lastMacd.dif?.[idx] ?? null
    const dea = lastMacd.dea?.[idx] ?? null
    const barVal = dif != null && dea != null ? (dif - dea) * 2 : null
    const c = barVal == null ? '#f0f4f8' : barVal >= 0 ? UP_COLOR : DOWN_COLOR
    out.push(`<div><span style="color:#38bdf8">DIF</span> ${fmtNum(dif, 3)} <span style="color:#f59e0b">DEA</span> ${fmtNum(dea, 3)} 柱 <b style="color:${c}">${fmtNum(barVal, 3)}</b></div>`)
  }
  if (ind.rsi && lastRsi) {
    out.push(`<div><span style="color:#f59e0b">RSI</span> ${fmtNum(lastRsi[idx] ?? null, 2)}</div>`)
  }
  if (ind.skdj && lastSkdj) {
    out.push(`<div><span style="color:#a78bfa">SKDJ</span> K ${fmtNum(lastSkdj.sk[idx] ?? null, 2)} D ${fmtNum(lastSkdj.sd[idx] ?? null, 2)}</div>`)
  }
  return out.join('')
}

// ── 图表初始化 ─────────────────────────────────────────────────────────────
function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value)

  const opt = buildOption(chartRef.value.clientHeight)
  chart.setOption(opt)

  setBarInfoByIndex(props.klines.length - 1)
  overlayCache = buildOverlayData()
  queueGraphic()

  chart.getZr().on('globalout', () => setBarInfoByIndex(props.klines.length - 1))
  chart.on('updateAxisPointer', (ev: unknown) => {
    const e = ev as { axesInfo?: { axisDim?: string; value?: string | number }[] }
    const infos = e?.axesInfo
    if (!infos?.length) return
    const xInfo = infos.find(a => a.axisDim === 'x')
    if (xInfo?.value == null) return
    const key = String(xInfo.value)
    let idx = lastDates.indexOf(key)
    if (idx < 0 && typeof xInfo.value === 'number' && Number.isInteger(xInfo.value)) idx = xInfo.value
    if (idx >= 0 && idx < props.klines.length) setBarInfoByIndex(idx)
  })
  chart.on('dataZoom', () => {
    const dz = (chart?.getOption() as { dataZoom?: DataZoomOption[] } | undefined)?.dataZoom?.[0]
    if (dz && dz.start != null && dz.end != null) emit('zoomChange', dz.start, dz.end)
    queueGraphic()
  })
  chart.on('finished', () => queueGraphic())
}

let lastKlineSig = ''

function getContainerHeight(): number {
  return chartRef.value?.clientHeight || 300
}

function applyKlineUpdate() {
  const sig = klineSeriesSignature(props.klines)
  if (sig === lastKlineSig) return
  lastKlineSig = sig
  updateChart()
}

function updateChart() {
  if (!chart) { initChart(); return }
  setChartOptionKeepDataZoom(chart, buildOption(getContainerHeight()), true)
  setBarInfoByIndex(displayKlines.value.length - 1)
  overlayCache = buildOverlayData()
  queueGraphic()
}

function updateMaIndicators() {
  if (!chart) return
  setChartOptionKeepDataZoom(chart, buildOption(getContainerHeight()), true)
  setBarInfoByIndex(displayKlines.value.length - 1)
}

function updateOverlayOnly() {
  overlayCache = buildOverlayData()
  queueGraphic()
}

function updateIndicatorOption() {
  if (!chart) return
  setChartOptionKeepDataZoom(chart, buildOption(getContainerHeight()), true)
  setBarInfoByIndex(displayKlines.value.length - 1)
  overlayCache = buildOverlayData()
  queueGraphic()
}

const onResize = useDebouncedCallback(() => {
  chart?.resize()
  queueGraphic()
}, 150)

onMounted(() => {
  initChart()
  window.addEventListener('resize', onResize)
  if (chartRef.value) {
    resizeObserver = new ResizeObserver(() => {
      chart?.resize()
      queueGraphic()
    })
    resizeObserver.observe(chartRef.value)
  }
})

onUnmounted(() => {
  cancelAnimationFrame(graphicRaf)
  resizeObserver?.disconnect()
  if (longPressTimer) clearTimeout(longPressTimer)
  if (chart) {
    chart.dispose()
    chart = null
  }
  window.removeEventListener('resize', onResize)
})

function onTouchStart(e: TouchEvent) {
  if (e.touches.length === 1) {
    touchCount = 1
    touchStartX = e.touches[0].clientX
    touchStartY = e.touches[0].clientY
    lastTouchX = touchStartX
    isScrolling = false
    longPressTimer = setTimeout(() => {
      if (!isScrolling && chart) {
        chart.dispatchAction({ type: 'showTip' })
      }
    }, LONG_PRESS_DELAY)
  } else if (e.touches.length === 2) {
    touchCount = 2
    if (longPressTimer) clearTimeout(longPressTimer)
  }
}

function onTouchMove(e: TouchEvent) {
  if (e.touches.length === 1) {
    const dx = e.touches[0].clientX - touchStartX
    const dy = e.touches[0].clientY - touchStartY
    if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > 10) {
      isScrolling = true
    }
    if (!isScrolling && chart) {
      const step = (touchStartX - e.touches[0].clientX) * 0.05
      if (Math.abs(step) >= 1) {
        chart.dispatchAction({ type: 'dataZoom', start: -step, end: step, dataZoomIndex: 0 })
        touchStartX = e.touches[0].clientX
      }
    }
    lastTouchX = e.touches[0].clientX
  } else if (e.touches.length === 2) {
    if (longPressTimer) clearTimeout(longPressTimer)
  }
}

function onTouchEnd(e: TouchEvent) {
  if (longPressTimer) clearTimeout(longPressTimer)
  longPressTimer = null
  if (e.changedTouches.length === 1 && !isScrolling) {
    const dx = e.changedTouches[0].clientX - touchStartX
    if (Math.abs(dx) < 5 && Math.abs(e.changedTouches[0].clientY - touchStartY) < 5) {
      if (chart) {
        chart.dispatchAction({ type: 'hideTip' })
      }
    }
  }
  touchCount = 0
  isScrolling = false
}

watch(
  () => props.klines,
  applyKlineUpdate
)

watch(
  () => [
    props.mergedKlines,
    props.bis, props.zhongshus, props.signals, props.xians, props.aiSignal, props.supportResistance,
  ],
  updateOverlayOnly,
  { deep: true }
)

watch(
  () => [
    props.indicators?.mergedKlines,
    props.indicators?.bis,
    props.indicators?.xians,
    props.indicators?.zhongshus,
    props.indicators?.signals,
    props.indicators?.aiLines,
    props.indicators?.supportResistance,
  ],
  updateOverlayOnly,
)

watch(
  () => [
    props.indicators?.ma5,
    props.indicators?.ma20,
    props.indicators?.ma60,
  ],
  updateMaIndicators,
)

watch(
  () => [
    props.indicators?.volume,
    props.indicators?.macd,
    props.indicators?.rsi,
    props.indicators?.skdj,
  ],
  updateIndicatorOption,
)
</script>

<style scoped>
.kline-wrap {
  width: 100%;
  height: 100%;
  border-radius: 10px;
  overflow: hidden;
  /* 与 ECharts 的 backgroundColor 一致：图表区是白底 */
  background: #ffffff;
  position: relative;
}

.kline-chart {
  width: 100%;
  height: 100%;
  opacity: 0;
  transition: opacity 0.22s ease;
}
.kline-chart.chart-ready { opacity: 1; }

.bar-info {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 6px 10px 8px;
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1.5;
  color: #57606a;
  border-top: 1px solid rgba(0, 0, 0, 0.1);
  background: #ffffff;
  word-break: break-all;
}

.chart-skeleton {
  position: absolute;
  inset: 0;
  padding: 12px 8px 8px;
  background: #ffffff;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}
.skel-header {
  display: flex;
  gap: 8px;
  align-items: center;
  padding-bottom: 4px;
}
.skel-bar {
  height: 14px;
  border-radius: 4px;
  background: linear-gradient(90deg, var(--bg-card) 25%, var(--bg-hover) 50%, var(--bg-card) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite;
}
.skel-bar.short { width: 20%; }
.skel-bar.medium { width: 35%; }
.skel-chart-area {
  flex: 1;
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  background: linear-gradient(90deg, var(--bg-secondary) 25%, var(--bg-hover) 50%, var(--bg-secondary) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite 0.2s;
}
.skel-candle {
  position: absolute;
  width: 5%;
  border-radius: 2px;
  background: var(--bg-hover);
  bottom: 0;
  opacity: 0.5;
}
.skel-ma-row {
  display: flex;
  gap: 12px;
  align-items: center;
  padding-top: 2px;
}
.skel-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: linear-gradient(90deg, var(--bg-card) 25%, var(--bg-hover) 50%, var(--bg-card) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite;
}
.skel-dot.ma5 { background: linear-gradient(90deg, rgba(239,68,68,0.4) 25%, rgba(239,68,68,0.15) 50%, rgba(239,68,68,0.4) 75%); background-size: 200% 100%; }
.skel-dot.ma20 { background: linear-gradient(90deg, rgba(34,197,94,0.4) 25%, rgba(34,197,94,0.15) 50%, rgba(34,197,94,0.4) 75%); background-size: 200% 100%; }
.skel-dot.ma60 { background: linear-gradient(90deg, rgba(56,189,248,0.4) 25%, rgba(56,189,248,0.15) 50%, rgba(56,189,248,0.4) 75%); background-size: 200% 100%; }

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
