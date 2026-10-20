/** Core ECharts capabilities shared by the dashboard charts. */
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  GraphicComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

let ready = false

export function ensureCoreCharts(): void {
  if (ready) return
  use([
    CanvasRenderer,
    LineChart,
    BarChart,
    PieChart,
    GridComponent,
    GraphicComponent,
    TooltipComponent,
    LegendComponent,
  ])
  ready = true
}
