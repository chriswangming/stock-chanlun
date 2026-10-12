<template>
  <div class="kline-wrap">
    <!-- 骨架屏：数据加载中时显示 -->
    <div v-if="loading" class="kline-skeleton" :style="{ height: `${chartHeightPx}px` }">
      <div class="skeleton-header">
        <div class="sk-bar sk-w-40" />
        <div class="sk-bar sk-w-20" />
        <div class="sk-bar sk-w-30" />
      </div>
      <div class="skeleton-candles">
        <div
          v-for="i in 40"
          :key="i"
          class="sk-candle"
          :style="{
            height: `${20 + ((i * 37 + 11) % 60)}%`,
          }"
        />
      </div>
      <div class="skeleton-footer">
        <div class="sk-bar sk-w-60" />
      </div>
    </div>

    <!-- 实际图表（主图 + 副图单实例多 grid） -->
    <div
      v-show="!loading"
      ref="chartRef"
      class="kline-chart"
      :class="{ 'chart-ready': !loading }"
      :style="{ height: `${chartHeightPx}px` }"
    />
    <div v-if="barInfoText" class="bar-info">{{ barInfoText }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import echarts from '../../utils/echarts'
import type { KLine, Bi, XianSegment, Zhongshu, Signal, AISignal, SupportResistance } from '../../api/stock'
import type { IndicatorConfig } from '../../stores/chanlun'
import { calcMA, computeDualMacdSkdjMarkerIndices } from '../../utils/stockIndicators'
import { downsampleKlines, klineSeriesSignature } from '../../utils/chartDownsample'
import { setChartOptionKeepDataZoom } from '../../utils/chartEchartsHelpers'
import { formatAxisLabel, formatStamp, normTs } from '../../utils/chartDateUtils'
import { useDebouncedCallback } from '../../composables/useDebounce'
import { useKlineIndicators } from '../../composables/useKlineIndicators'
import {
  type ChanlunOverlayPayload,
  CHANLUN_OVERLAY_THEME_PC,
  buildChanlunGraphicChildren,
  buildChanlunOverlayCache,
  resolveDataZoomViewRange,
} from '../../utils/chartOverlayCore'

const props = defineProps<{
  klines: KLine[]
  bis: Bi[]
  zhongshus: Zhongshu[]
  signals: Signal[]
  xians?: XianSegment[]
  aiSignal?: AISignal | null
  supportResistance?: SupportResistance[]
  indicators?: IndicatorConfig
  loading?: boolean
}>()

const chartRef = ref<HTMLDivElement | null>(null)
const barInfoText = ref('')
let chart: echarts.ECharts | null = null

/** 主图渲染用降采样序列；缠论叠加仍基于全量 props */
const displayKlines = computed(() => downsampleKlines(props.klines))
const klineIndicators = useKlineIndicators(displayKlines)

/** 获取指标配置，如果未提供则默认全部显示 */
function getIndicators(): Required<IndicatorConfig> {
  return {
    ma5: true, ma20: true, ma60: true,
    bis: true, xians: false, zhongshus: true, signals: true, aiLines: true,
    supportResistance: true,
    volume: true, macd: true, rsi: true, skdj: true,
    ...(props.indicators || {})
  }
}

/** buildOption 写入、renderItem 读取 —— ECharts 对 data 里的大对象不可靠，必须走闭包缓存 */
type DataZoomOption = { startValue?: number; endValue?: number; start?: number; end?: number }

let chanlunOverlayCache: ChanlunOverlayPayload | null = null

const COLOR_UP = '#f85149'
const COLOR_DOWN = '#3fb950'
const MAIN_TOP = 36
const MAIN_HEIGHT = 420
const SUB_PANEL_HEIGHT = { volume: 120, macd: 120, rsi: 100, skdj: 100 } as const
const SUB_PANEL_ORDER = ['volume', 'macd', 'rsi', 'skdj'] as const
type SubPanelKey = (typeof SUB_PANEL_ORDER)[number]
const SUB_GAP = 4
const SLIDER_BOTTOM = 5
const SLIDER_HEIGHT = 20

function canRenderSubPanel(key: SubPanelKey, barCount: number, ind: Required<IndicatorConfig>): boolean {
  if (!ind[key]) return false
  if (key === 'volume') return barCount >= 1
  if (key === 'macd') return barCount >= 30
  if (key === 'rsi') return barCount >= 20
  if (key === 'skdj') return barCount >= 12
  return false
}

function activeSubPanels(ind: Required<IndicatorConfig>, barCount: number): SubPanelKey[] {
  return SUB_PANEL_ORDER.filter(k => canRenderSubPanel(k, barCount, ind))
}

const chartHeightPx = computed(() => {
  const n = displayKlines.value.length
  const ind = getIndicators()
  const subs = activeSubPanels(ind, n)
  const subHeights = subs.reduce((sum, k) => sum + SUB_PANEL_HEIGHT[k], 0)
  const gaps = subs.length > 0 ? SUB_GAP * subs.length : 0
  return MAIN_TOP + MAIN_HEIGHT + subHeights + gaps + SLIDER_BOTTOM + SLIDER_HEIGHT + 8
})

function fmtPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(2)
}

let lastDates: string[] = []
let lastMa5: (number | null)[] = []
let lastMa20: (number | null)[] = []
let lastMa60: (number | null)[] = []

function formatBarLine(idx: number): string {
  const series = displayKlines.value
  if (idx < 0 || idx >= series.length) return ''
  const k = series[idx]
  const d = formatStamp(lastDates[idx] ?? k.date)
  return `${d}  开盘 ${fmtPrice(k.open)}  收盘 ${fmtPrice(k.close)}  最高 ${fmtPrice(k.high)}  最低 ${fmtPrice(k.low)}  |  MA5 ${fmtPrice(lastMa5[idx])}  MA20 ${fmtPrice(lastMa20[idx])}  MA60 ${fmtPrice(lastMa60[idx])}`
}

function setBarInfoByIndex(idx: number) {
  const series = displayKlines.value
  if (!series.length) { barInfoText.value = ''; return }
  const i = Math.max(0, Math.min(idx, series.length - 1))
  barInfoText.value = formatBarLine(i)
}

function buildOption() {
  const seriesKlines = displayKlines.value
  // 轴类目必须用完整时间戳：分钟级周期同一天有多根 bar，只取日期会出现重复类目，
  // convertToPixel 与缠论索引都会定位错。
  const dates = seriesKlines.map(k => normTs(k.date))
  const ohlc = seriesKlines.map(k => [k.open, k.close, k.low, k.high])
  const closes = seriesKlines.map(k => k.close)
  const ind = getIndicators()
  const indData = klineIndicators.value
  const ma5 = ind.ma5 ? calcMA(closes, 5) : []
  const ma20 = ind.ma20 ? calcMA(closes, 20) : []
  const ma60 = ind.ma60 ? calcMA(closes, 60) : []

  lastDates = dates; lastMa5 = ma5; lastMa20 = ma20; lastMa60 = ma60

  syncChanlunOverlayCache()

  const lineSeriesOpts = {
    type: 'line' as const, xAxisIndex: 0, yAxisIndex: 0,
    smooth: true, showSymbol: false, connectNulls: false, z: 4
  }

  const legendData = ['K线']
  const seriesList: Record<string, unknown>[] = [
    {
      name: 'K线', type: 'candlestick', data: ohlc,
      xAxisIndex: 0, yAxisIndex: 0, z: 3,
      itemStyle: {
        color: '#f85149', color0: '#3fb950',
        borderColor: '#f85149', borderColor0: '#3fb950'
      }
    }
  ]
  if (ind.ma5) { legendData.push('MA5'); seriesList.push({ name: 'MA5', ...lineSeriesOpts, data: ma5, lineStyle: { width: 1, color: '#f0b429' }, emphasis: { disabled: true } }) }
  if (ind.ma20) { legendData.push('MA20'); seriesList.push({ name: 'MA20', ...lineSeriesOpts, data: ma20, lineStyle: { width: 1, color: '#58a6ff' }, emphasis: { disabled: true } }) }
  if (ind.ma60) { legendData.push('MA60'); seriesList.push({ name: 'MA60', ...lineSeriesOpts, data: ma60, lineStyle: { width: 1, color: '#bc8cff' }, emphasis: { disabled: true } }) }

  const subs = activeSubPanels(ind, seriesKlines.length)
  const grids: Record<string, unknown>[] = []
  const xAxes: Record<string, unknown>[] = []
  const yAxes: Record<string, unknown>[] = []

  if (subs.length === 0) {
    grids.push({ left: 10, right: 10, top: MAIN_TOP, bottom: 40 })
  } else {
    grids.push({ left: 10, right: 10, top: MAIN_TOP, height: MAIN_HEIGHT })
  }

  xAxes.push({
    type: 'category',
    data: dates,
    gridIndex: 0,
    boundaryGap: true,
    axisLine: { lineStyle: { color: '#30363d' } },
    axisTick: { show: false },
    axisLabel: {
      color: '#7d8590', fontSize: 10, interval: 'auto' as const, show: subs.length === 0,
      formatter: (v: string) => formatAxisLabel(v),
    },
    splitLine: { show: false },
  })
  yAxes.push({
    scale: true,
    gridIndex: 0,
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: 'rgba(33,38,45,0.35)', type: 'dashed' } },
    axisLabel: { color: '#7d8590', fontSize: 10 },
    axisPointer: { label: { show: true, color: '#e6edf3', backgroundColor: '#30363d' } },
  })

  let subTop = MAIN_TOP + MAIN_HEIGHT + (subs.length ? SUB_GAP : 0)
  for (let i = 0; i < subs.length; i++) {
    const key = subs[i]
    const gridIndex = i + 1
    const h = SUB_PANEL_HEIGHT[key]
    grids.push({ left: 10, right: 10, top: subTop, height: h })
    subTop += h + SUB_GAP

    const isLastSub = i === subs.length - 1
    xAxes.push({
      type: 'category',
      data: dates,
      gridIndex,
      boundaryGap: true,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisTick: { show: false },
      axisLabel: {
        show: isLastSub, color: '#7d8590', fontSize: 9, interval: 'auto' as const,
        formatter: (v: string) => formatAxisLabel(v),
      },
      splitLine: { show: false },
    })

    if (key === 'volume') {
      yAxes.push({
        scale: true,
        gridIndex,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
        axisLabel: {
          color: '#7d8590',
          fontSize: 9,
          formatter: (v: number) => {
            if (v >= 1e8) return `${(v / 1e8).toFixed(1)}亿`
            if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`
            return String(Math.round(v))
          },
        },
      })
      const colors = seriesKlines.map((k, idx) => {
        if (idx === 0) return k.close >= k.open ? COLOR_UP : COLOR_DOWN
        return k.close >= seriesKlines[idx - 1].close ? COLOR_UP : COLOR_DOWN
      })
      seriesList.push({
        name: '成交量',
        type: 'bar',
        xAxisIndex: gridIndex,
        yAxisIndex: gridIndex,
        data: seriesKlines.map((k, idx) => ({ value: k.volume ?? 0, itemStyle: { color: colors[idx] } })),
        barMaxWidth: 4,
      })
    } else if (key === 'macd') {
      const macd = indData.macd
      if (!macd) continue
      const { dif, dea } = macd
      const bar = dif.map((v, idx) => (v - dea[idx]) * 2)
      yAxes.push({
        scale: true,
        gridIndex,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
        axisLabel: { color: '#7d8590', fontSize: 9 },
      })
      seriesList.push(
        { name: 'DIF', type: 'line', xAxisIndex: gridIndex, yAxisIndex: gridIndex, data: dif, lineStyle: { color: '#58a6ff', width: 1 }, showSymbol: false,
          markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: '#7d8590', width: 1, opacity: 0.55 }, data: [{ yAxis: 0 }] } },
        { name: 'DEA', type: 'line', xAxisIndex: gridIndex, yAxisIndex: gridIndex, data: dea, lineStyle: { color: '#d29922', width: 1 }, showSymbol: false },
        // 正负分两条 series 只为上色：负值必须保持负号，否则会被画到 0 轴上方。
        // 两条同 stack 才会落在同一槽位（不 stack 时 ECharts 会把它们并排错开）。
        { name: 'MACD', type: 'bar', stack: 'macd', xAxisIndex: gridIndex, yAxisIndex: gridIndex, data: bar.map(v => (v >= 0 ? v : 0)), itemStyle: { color: COLOR_UP }, barMaxWidth: 4 },
        { name: 'MACDneg', type: 'bar', stack: 'macd', xAxisIndex: gridIndex, yAxisIndex: gridIndex, data: bar.map(v => (v < 0 ? v : 0)), itemStyle: { color: COLOR_DOWN }, barMaxWidth: 4 },
      )
    } else if (key === 'rsi') {
      const rsi = indData.rsi
      if (!rsi) continue
      yAxes.push({
        min: 0,
        max: 100,
        scale: false,
        gridIndex,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
        axisLabel: { color: '#7d8590', fontSize: 9 },
      })
      seriesList.push({
        name: 'RSI',
        type: 'line',
        xAxisIndex: gridIndex,
        yAxisIndex: gridIndex,
        data: rsi,
        lineStyle: { color: '#bc8cff', width: 1.5 },
        showSymbol: false,
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { type: 'dashed', width: 1 },
          data: [
            { yAxis: 70, lineStyle: { color: COLOR_UP, opacity: 0.5 }, label: { show: false } },
            { yAxis: 30, lineStyle: { color: COLOR_DOWN, opacity: 0.5 }, label: { show: false } },
            { yAxis: 50, lineStyle: { color: '#484f58', opacity: 0.3 }, label: { show: false } },
          ],
        },
      })
    } else if (key === 'skdj') {
      const sk = indData.sk
      const sd = indData.sd
      if (!sk || !sd) continue
      yAxes.push({
        min: 0,
        max: 100,
        scale: false,
        gridIndex,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
        axisLabel: { color: '#7d8590', fontSize: 9 },
      })
      seriesList.push(
        {
          name: 'SK',
          type: 'line',
          xAxisIndex: gridIndex,
          yAxisIndex: gridIndex,
          data: sk,
          connectNulls: false,
          showSymbol: false,
          lineStyle: { color: '#58a6ff', width: 1.2 },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { type: 'dashed', width: 1 },
            data: [
              { yAxis: 80, lineStyle: { color: COLOR_UP, opacity: 0.45 }, label: { show: false } },
              { yAxis: 20, lineStyle: { color: COLOR_DOWN, opacity: 0.45 }, label: { show: false } },
              { yAxis: 50, lineStyle: { color: '#484f58', opacity: 0.25 }, label: { show: false } },
            ],
          },
        },
        {
          name: 'SD',
          type: 'line',
          xAxisIndex: gridIndex,
          yAxisIndex: gridIndex,
          data: sd,
          connectNulls: false,
          showSymbol: false,
          lineStyle: { color: '#d29922', width: 1.2 },
        },
      )
    }
  }

  const xAxisIndexes = grids.map((_, idx) => idx)

  return {
    animation: false,
    legend: {
      data: legendData,
      top: 2, left: 'center',
      textStyle: { color: '#7d8590', fontSize: 11 },
      itemWidth: 14, itemHeight: 8
    },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: xAxisIndexes, start: 70, end: 100 },
      {
        type: 'slider',
        xAxisIndex: xAxisIndexes,
        start: 70,
        end: 100,
        height: SLIDER_HEIGHT,
        bottom: SLIDER_BOTTOM,
        borderColor: '#30363d',
        fillerColor: 'rgba(88,166,255,0.1)',
        handleStyle: { color: '#58a6ff' },
        textStyle: { color: '#7d8590' },
      },
    ],
    series: seriesList,
    tooltip: {
      trigger: 'axis', axisPointer: {
        type: 'cross',
        lineStyle: { color: '#484f58' },
        label: {
          show: true, backgroundColor: '#30363d', color: '#e6edf3',
          formatter: (p: { axisDimension?: string; value?: unknown }) => {
            if (p.axisDimension === 'y' && p.value != null) return Number(p.value).toFixed(2)
            return p.value != null ? String(p.value) : ''
          }
        }
      },
      backgroundColor: '#1c2128', borderColor: '#30363d',
      textStyle: { color: '#e6edf3', fontSize: 12 },
      formatter: (params: unknown) => {
        const seriesKlines = displayKlines.value
        const arr = Array.isArray(params) ? params : [params]
        if (!arr.length) return ''
        const first = arr[0] as { dataIndex?: number }
        const idx = first.dataIndex
        if (idx == null || idx < 0 || idx >= seriesKlines.length) return ''
        const k = seriesKlines[idx]
        const d = formatStamp(k.date)
        const change = k.close >= k.open
        const changeColor = change ? '#3fb950' : '#f85149'
        const changePct = k.open > 0 ? ((k.close - k.open) / k.open * 100) : 0

        let html = `<div style="font-weight:600;margin-bottom:8px;border-bottom:1px solid #30363d;padding-bottom:6px">${d}</div>`

        // 价格信息
        html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 16px">`
        html += `<div>开盘 <b>${fmtPrice(k.open)}</b></div>`
        html += `<div style="color:${changeColor}">收盘 <b>${fmtPrice(k.close)}</b></div>`
        html += `<div>最高 ${fmtPrice(k.high)}</div>`
        html += `<div>最低 ${fmtPrice(k.low)}</div>`
        html += `</div>`

        // 涨跌幅
        html += `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #30363d;color:${changeColor}">`
        html += `涨跌额 ${change ? '+' : ''}${(k.close - k.open).toFixed(2)}　`
        html += `涨跌幅 ${change ? '+' : ''}${changePct.toFixed(2)}%`
        html += `</div>`

        // 均线
        html += `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #30363d">`
        html += `<div style="margin-bottom:4px;color:#7d8590">均线</div>`
        html += `<span style="color:#f0b429">●</span> MA5 ${fmtPrice(ma5[idx])}　`
        html += `<span style="color:#58a6ff">●</span> MA20 ${fmtPrice(ma20[idx])}　`
        html += `<span style="color:#bc8cff">●</span> MA60 ${fmtPrice(ma60[idx])}`
        html += `</div>`

        return html
      }
    }
  }
}

/** 用 graphic + convertToPixel 画缠论（custom series 在部分环境下不渲染） */
let graphicRaf = 0

function pixelAt(dateStr: string, price: number): [number, number] | null {
  if (!chart) return null
  try {
    const pt = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [dateStr, price]) as unknown
    if (!Array.isArray(pt) || pt.length < 2) return null
    const x = Number(pt[0])
    const y = Number(pt[1])
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null
    return [x, y]
  } catch {
    return null
  }
}

function pixelAtIdx(i: number, price: number): [number, number] | null {
  if (i < 0 || i >= lastDates.length) return null
  return pixelAt(lastDates[i], price)
}

function applyChanlunGraphic() {
  if (!chart) return
  const data = chanlunOverlayCache
  if (!data || data._n <= 0 || !lastDates.length) {
    chart.setOption({ graphic: [] }, { replaceMerge: ['graphic'] })
    return
  }

  const opt = chart.getOption() as { dataZoom?: DataZoomOption[] }
  const { viewS, viewE } = resolveDataZoomViewRange(lastDates.length, opt?.dataZoom)
  const finder = { xAxisIndex: 0, yAxisIndex: 0 } as const

  let gridLeft = 0
  let gridRight = 800
  try {
    const mid = props.klines[Math.floor(props.klines.length / 2)]?.close ?? 1
    const p0 = chart.convertToPixel(finder, [lastDates[0], mid]) as number[]
    const p1 = chart.convertToPixel(finder, [lastDates[lastDates.length - 1], mid]) as number[]
    if (p0?.length >= 1 && p1?.length >= 1 && Number.isFinite(p0[0]) && Number.isFinite(p1[0])) {
      gridLeft = Math.min(p0[0], p1[0])
      gridRight = Math.max(p0[0], p1[0])
    }
  } catch { /* ignore */ }

  const children = buildChanlunGraphicChildren({
    data,
    viewS,
    viewE,
    gridLeft,
    gridRight,
    pixelAtIdx,
    theme: CHANLUN_OVERLAY_THEME_PC,
    priceAtIdx: i => displayKlines.value[i]?.close ?? 0,
  })

  chart.setOption({
    graphic: [{ id: 'chanlun-overlay', type: 'group', children, z: 100, silent: true }]
  }, { replaceMerge: ['graphic'] })
}

function queueChanlunGraphic() {
  if (!chart) return
  cancelAnimationFrame(graphicRaf)
  graphicRaf = requestAnimationFrame(() => {
    graphicRaf = 0
    applyChanlunGraphic()
  })
}

function onChartFinished() {
  queueChanlunGraphic()
}

function onChartDataZoom() {
  queueChanlunGraphic()
}

function onAxisPointerUpdate(ev: unknown) {
  const e = ev as { axesInfo?: { axisDim?: string; value?: string | number }[] }
  const infos = e?.axesInfo
  if (!infos?.length) return
  const xInfo = infos.find(a => a.axisDim === 'x')
  if (xInfo?.value == null) return
  const key = String(xInfo.value)
  let idx = lastDates.indexOf(key)
  if (idx < 0 && typeof xInfo.value === 'number' && Number.isInteger(xInfo.value)) idx = xInfo.value
  const n = displayKlines.value.length
  if (idx >= 0 && idx < n) setBarInfoByIndex(idx)
}

function onChartGlobalOut() {
  const n = displayKlines.value.length
  if (n > 0) setBarInfoByIndex(n - 1)
}

function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value)
  chart.setOption(buildOption())
  setBarInfoByIndex(displayKlines.value.length - 1)
  chart.getZr().on('globalout', onChartGlobalOut)
  chart.on('updateAxisPointer', onAxisPointerUpdate)
  chart.on('finished', onChartFinished)
  chart.on('dataZoom', onChartDataZoom)
  queueChanlunGraphic()
}

function updateChart() {
  if (!chart) return
  setChartOptionKeepDataZoom(chart, buildOption(), true)
  setBarInfoByIndex(displayKlines.value.length - 1)
  queueChanlunGraphic()
}

/**
 * 局部更新：缠论叠加层数据变化时，只重绘 graphic，不重建整个 chart option。
 */
function updateOverlayOnly() {
  if (!chart) return
  syncChanlunOverlayCache()
  queueChanlunGraphic()
}

/** 仅刷新缠论叠加缓存与 graphic，不重建 K 线/均线 series */
function syncChanlunOverlayCache() {
  const seriesKlines = displayKlines.value
  const dates = seriesKlines.map(k => normTs(k.date))
  const ind = getIndicators()
  const indData = klineIndicators.value

  let dualCrossIndices: number[] = []
  if (seriesKlines.length >= 30 && ind.macd && ind.skdj && indData.macd && indData.sk && indData.sd) {
    dualCrossIndices = computeDualMacdSkdjMarkerIndices(seriesKlines, 3, 1e-9, {
      dif: indData.macd.dif,
      dea: indData.macd.dea,
      sk: indData.sk,
      sd: indData.sd,
    }).indices
  }

  chanlunOverlayCache = buildChanlunOverlayCache({
    dates,
    seriesKlines,
    bis: props.bis,
    xians: props.xians,
    zhongshus: props.zhongshus,
    signals: props.signals,
    aiSignal: props.aiSignal,
    supportResistance: props.supportResistance,
    flags: {
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

function updateMaIndicators() {
  if (!chart) return
  setChartOptionKeepDataZoom(chart, buildOption(), true)
  setBarInfoByIndex(displayKlines.value.length - 1)
}

/** 副图增删时重建 option，保留 dataZoom */
function updateIndicatorOption() {
  if (!chart) return
  setChartOptionKeepDataZoom(chart, buildOption(), true)
  setBarInfoByIndex(displayKlines.value.length - 1)
  queueChanlunGraphic()
}

const onResize = useDebouncedCallback(() => {
  chart?.resize()
  queueChanlunGraphic()
}, 150)

onMounted(() => {
  initChart()
  window.addEventListener('resize', onResize)
})

onUnmounted(() => {
  cancelAnimationFrame(graphicRaf)
  graphicRaf = 0
  if (chart) {
    chart.off('updateAxisPointer', onAxisPointerUpdate)
    chart.off('finished', onChartFinished)
    chart.off('dataZoom', onChartDataZoom)
    chart.getZr().off('globalout', onChartGlobalOut)
    chart.dispose()
    chart = null
  }
  window.removeEventListener('resize', onResize)
})

let lastKlineSig = ''

function applyKlineUpdate() {
  if (!chart) return
  setChartOptionKeepDataZoom(chart, buildOption(), true)
  setBarInfoByIndex(displayKlines.value.length - 1)
  queueChanlunGraphic()
}

watch(
  () => props.klines,
  (kl) => {
    const sig = klineSeriesSignature(kl)
    if (sig === lastKlineSig) return
    lastKlineSig = sig
    applyKlineUpdate()
  }
)

watch(
  () => [props.bis, props.zhongshus, props.signals, props.xians, props.aiSignal, props.supportResistance],
  updateOverlayOnly,
  { deep: true }
)

/** 缠论/AI 线等仅影响 graphic，不必重建 K 线 series */
watch(
  () => [
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

watch(chartHeightPx, () => {
  onResize()
})
</script>

<style scoped>
.kline-wrap {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  background: var(--bg-card);
}
.kline-chart {
  width: 100%;
  min-height: 420px;
  opacity: 0;
  transition: opacity 0.22s ease;
}
.kline-chart.chart-ready { opacity: 1; }
.bar-info {
  padding: 8px 12px 10px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary, #7d8590);
  border-top: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}

/* ── 骨架屏 ── */
.kline-skeleton {
  width: 100%;
  min-height: 420px;
  padding: 16px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.skeleton-header {
  display: flex;
  gap: 8px;
  align-items: center;
}
.sk-bar {
  height: 10px;
  border-radius: 4px;
  background: var(--border);
  animation: shimmer 1.4s ease-in-out infinite;
}
.sk-w-20 { width: 20%; }
.sk-w-30 { width: 30%; }
.sk-w-40 { width: 40%; }
.sk-w-60 { width: 60%; }
.skeleton-candles {
  flex: 1;
  display: flex;
  align-items: flex-end;
  gap: 2px;
  padding: 0 4px;
}
.sk-candle {
  flex: 1;
  border-radius: 2px 2px 0 0;
  background: var(--text-muted);
  animation: shimmer 1.4s ease-in-out infinite;
  animation-delay: calc(var(--i, 0) * 30ms);
}
.skeleton-footer {
  display: flex;
}
@keyframes shimmer {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 0.9; }
}
</style>
