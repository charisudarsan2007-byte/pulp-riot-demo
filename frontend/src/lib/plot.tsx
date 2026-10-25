import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import { useApp } from './state'

const Plot = createPlotlyComponent(Plotly as any)

export function useChartTheme() {
  const { dark } = useApp()
  return dark
    ? { fg: '#f5f5dc', soft: '#c9c7b0', grid: 'rgba(245,245,220,0.12)', line: 'rgba(245,245,220,0.35)', bg: 'rgba(0,0,0,0)', surface: '#141414', band: 'rgba(209,173,107,0.28)', bronze: '#d1ad6b',
        red: '#f0a08c', amber: '#e8c066', green: '#a7d3ae', ink: '#f5f5dc', slate: '#9aa5ad' }
    : { fg: '#1a1a1a', soft: '#4d4a3f', grid: 'rgba(26,26,26,0.10)', line: 'rgba(26,26,26,0.35)', bg: 'rgba(0,0,0,0)', surface: '#ffffff', band: 'rgba(138,109,59,0.24)', bronze: '#8a6d3b',
        red: '#8f2f1c', amber: '#b7791f', green: '#3f6b49', ink: '#1a1a1a', slate: '#5b6770' }
}

export function Chart({ data, layout, height = 360, onClick, config }: { data: any[]; layout?: any; height?: number; onClick?: (e: any) => void; config?: any }) {
  const t = useChartTheme()
  const base = {
    autosize: true, transition: { duration: 500, easing: 'cubic-in-out' }, height, paper_bgcolor: t.bg, plot_bgcolor: t.bg, margin: { l: 56, r: 16, t: 24, b: 44 },
    font: { family: 'Inter, system-ui, sans-serif', size: 12, color: t.fg },
    xaxis: { gridcolor: t.grid, linecolor: t.line, zeroline: false, tickfont: { color: t.soft } },
    yaxis: { gridcolor: t.grid, linecolor: t.line, zeroline: false, tickfont: { color: t.soft } },
    legend: { orientation: 'h', y: -0.18, font: { size: 11, color: t.soft } },
    hoverlabel: { font: { family: 'Inter, system-ui, sans-serif', size: 12 } },
  }
  const merged = { ...base, ...layout, xaxis: { ...base.xaxis, ...(layout?.xaxis || {}) }, yaxis: { ...base.yaxis, ...(layout?.yaxis || {}) }, margin: { ...base.margin, ...(layout?.margin || {}) } }
  return <div className="chart-reveal"><Plot data={data} layout={merged} style={{ width: '100%', height }} useResizeHandler config={{ displayModeBar: false, responsive: true, ...config }} onClick={onClick} /></div>
}

export { Plot }
