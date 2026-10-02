import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'

// Modular imports: only what is used ends up in the bundle.
echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

export interface ChartPoint {
  label: string
  value: number // for drawing only
  display: string // the exact, already formatted text shown in the tooltip
}

interface TooltipParams {
  dataIndex: number
}

export function DailyChart({ points }: { points: readonly ChartPoint[] }) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!host.current) return
    const chart = echarts.init(host.current)
    chart.setOption({
      grid: { left: 48, right: 12, top: 16, bottom: 28 },
      tooltip: {
        trigger: 'axis',
        // The default tooltip builds HTML with inline `style` attributes, which a strict CSP blocks. This one is
        // plain text. ECharts inserts the returned string as HTML, so anything derived from user data must be
        // escaped here: this is an XSS sink as much as dangerouslySetInnerHTML is.
        formatter: (params: TooltipParams | TooltipParams[]) => {
          const first = Array.isArray(params) ? params[0] : params
          const point = first ? points[first.dataIndex] : undefined
          if (!point) return ''
          return `${echarts.format.encodeHTML(point.label)}<br/>${echarts.format.encodeHTML(point.display)}`
        },
      },
      xAxis: { type: 'category', data: points.map((p) => p.label) },
      yAxis: { type: 'value' },
      series: [{ type: 'bar', data: points.map((p) => p.value) }],
    })
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [points])

  return <div ref={host} role="img" aria-label="Daily net P&L" style={{ width: '100%', height: 260 }} />
}

export default DailyChart
