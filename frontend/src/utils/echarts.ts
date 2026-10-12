/**
 * ECharts 按需注册（全项目共用，避免各图表重复打包完整 echarts）
 */
import * as echarts from 'echarts/core'
import { BarChart, CandlestickChart, LineChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  TooltipComponent,
  // 未注册时 markLine 会被静默忽略：RSI/SKDJ 阈值线、MACD 0 轴都依赖它
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  CandlestickChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  GraphicComponent,
  MarkLineComponent,
  CanvasRenderer,
])

export default echarts
export type { ECharts } from 'echarts/core'
