import type { DailyResult } from '../../lib/data/model'
import { formatDay, formatPnl } from '../../lib/format'
import { toChartNumber } from '../../lib/money'
import { linear } from './scale'

const STEP = 30
const BAR = 20
const HEIGHT = 200
const MID = HEIGHT / 2
const PAD = 8

/** One bar per trading day, up for a gain and down for a loss. Each bar carries its own text for hover and screen readers. */
export function DailyBars({ days }: { days: readonly DailyResult[] }) {
  if (days.length === 0) return <p className="py-10 text-center text-muted">No trading days in this range.</p>
  const values = days.map((d) => toChartNumber(d.net_pnl))
  const biggest = Math.max(1, ...values.map((v) => Math.abs(v)))
  const size = linear([0, biggest], [0, MID - PAD])
  const best = days.reduce((a, b) => (toChartNumber(b.net_pnl) > toChartNumber(a.net_pnl) ? b : a))
  const worst = days.reduce((a, b) => (toChartNumber(b.net_pnl) < toChartNumber(a.net_pnl) ? b : a))
  const label = `Bar chart of daily profit and loss over ${days.length} trading days. Best day ${formatDay(best.day)}, ${formatPnl(best.net_pnl)}. Worst day ${formatDay(worst.day)}, ${formatPnl(worst.net_pnl)}.`
  return (
    <svg
      viewBox={`0 0 ${days.length * STEP} ${HEIGHT}`}
      width="100%"
      role="img"
      aria-label={label}
      className="mt-4 block"
    >
      <line x1={0} x2={days.length * STEP} y1={MID} y2={MID} className="stroke-line-strong" />
      {days.map((d, i) => {
        const v = values[i] ?? 0
        const h = Math.max(1, size(Math.abs(v)))
        return (
          <rect
            key={d.day}
            x={i * STEP + (STEP - BAR) / 2}
            y={v >= 0 ? MID - h : MID}
            width={BAR}
            height={h}
            rx={2}
            className={v >= 0 ? 'fill-pos' : 'fill-neg'}
          >
            <title>{`${formatDay(d.day)}: ${formatPnl(d.net_pnl)} (${d.trade_count} ${d.trade_count === 1 ? 'trade' : 'trades'})`}</title>
          </rect>
        )
      })}
    </svg>
  )
}
