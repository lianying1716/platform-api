/** Sample-only chart types; imported exclusively by /design-preview. */
import {
  FunnelChart,
  GaugeChart,
  HeatmapChart,
  RadarChart,
  SankeyChart,
  ScatterChart,
} from 'echarts/charts'
import {
  CalendarComponent,
  DataZoomComponent,
  MarkLineComponent,
  RadarComponent,
  VisualMapComponent,
} from 'echarts/components'
import { use } from 'echarts/core'
import { ensureCoreCharts } from './echarts-registry'

let ready = false

export function ensureAllCharts(): void {
  ensureCoreCharts()
  if (ready) return
  use([
    ScatterChart,
    HeatmapChart,
    FunnelChart,
    RadarChart,
    SankeyChart,
    GaugeChart,
    DataZoomComponent,
    MarkLineComponent,
    CalendarComponent,
    VisualMapComponent,
    RadarComponent,
  ])
  ready = true
}
