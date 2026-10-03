import type { EquityPoint } from '../../lib/data/model'
import { formatAmount, formatAxisMoney, formatDay, formatPnl } from '../../lib/format'
import { toChartNumber } from '../../lib/money'
import { linePath, linear, niceTicks, spreadIndices } from './scale'
import { useMeasuredWidth } from './useMeasuredWidth'

const GUTTER = 52 // room for the y-axis labels
const RIGHT = 8

function dayOf(at: string): string {
  return formatDay(at.slice(0, 10))
}

/** Cumulative net P&L: a line and a soft fill down to $0. Green when it ends up, red when it ends down. */
export function EquityChart({ points, unit }: { points: readonly EquityPoint[]; unit: 'trade' | 'day' }) {
  const [ref, W] = useMeasuredWidth<HTMLDivElement>()
  if (points.length === 0) return <p className="py-10 text-center text-muted">No trades in this range.</p>
  const H = 270
  const top = 12
  const bottom = 232
  const values = points.map((p) => toChartNumber(p.equity))
  const lo = Math.min(0, ...values)
  const hi = Math.max(0, ...values)
  const ticks = niceTicks(lo, hi, 3)
  const y = linear([Math.min(lo, ticks[0] ?? lo), Math.max(hi, ticks[ticks.length - 1] ?? hi)], [bottom, top])
  const x = linear([0, Math.max(1, points.length - 1)], [GUTTER, W - RIGHT])
  const line: [number, number][] = values.map((v, i) => [x(i), y(v)])
  const first = line[0] ?? [GUTTER, y(0)]
  const last = line[line.length - 1] ?? first
  const area = `${linePath(line)} L${last[0].toFixed(1)},${y(0).toFixed(1)} L${first[0].toFixed(1)},${y(0).toFixed(1)} Z`
  const finalEquity = points[points.length - 1]?.equity ?? '0'
  const ending = toChartNumber(finalEquity) >= 0
  const worst = points.reduce((low, p) => Math.min(low, toChartNumber(p.drawdown)), 0)
  const label =
    `Equity curve: ${formatPnl(finalEquity)} cumulative net over ${points.length} ${unit}${points.length === 1 ? '' : 's'}` +
    (worst < 0 ? `, deepest drawdown ${formatAmount(String(-worst))}` : '')
  return (
    <div ref={ref}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-label={label}
        className="mt-2 block font-mono text-[11px]"
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={GUTTER}
              x2={W - RIGHT}
              y1={y(t)}
              y2={y(t)}
              className={t === 0 ? 'stroke-line-strong' : 'stroke-line-soft'}
            />
            <text x={0} y={y(t) + 4} className="fill-muted">
              {formatAxisMoney(t)}
            </text>
          </g>
        ))}
        <path d={area} className={ending ? 'fill-pos/[0.12]' : 'fill-neg/[0.12]'} />
        <path
          d={linePath(line)}
          fill="none"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
          className={ending ? 'stroke-pos' : 'stroke-neg'}
        />
        {spreadIndices(points.length, 5).map((i, n, all) => (
          <text
            key={i}
            x={x(i)}
            y={256}
            textAnchor={n === 0 ? 'start' : n === all.length - 1 ? 'end' : 'middle'}
            className="fill-muted"
          >
            {dayOf(points[i]?.at ?? '')}
          </text>
        ))}
      </svg>
    </div>
  )
}

/** How far below its peak the account was after each trade (zero or negative). */
export function DrawdownChart({ points }: { points: readonly EquityPoint[] }) {
  const [ref, W] = useMeasuredWidth<HTMLDivElement>()
  if (points.length === 0) return null
  const H = 90
  const values = points.map((p) => toChartNumber(p.drawdown))
  const worst = Math.min(0, ...values)
  const y = linear([worst, 0], [H - 12, 10])
  const x = linear([0, Math.max(1, points.length - 1)], [GUTTER, W - RIGHT])
  const line: [number, number][] = values.map((v, i) => [x(i), y(v)])
  const first = line[0] ?? [GUTTER, y(0)]
  const last = line[line.length - 1] ?? first
  const area = `${linePath(line)} L${last[0].toFixed(1)},${y(0).toFixed(1)} L${first[0].toFixed(1)},${y(0).toFixed(1)} Z`
  const label =
    worst < 0
      ? `Drawdown chart, deepest point ${formatAmount(String(-worst))} below peak`
      : 'Drawdown chart: no drawdown in this range'
  return (
    <div ref={ref}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-label={label}
        className="mt-1 block font-mono text-[11px]"
      >
        <text x={0} y={22} className="fill-muted">
          $0
        </text>
        {worst < 0 ? (
          <text x={0} y={H - 12} className="fill-muted">
            {formatAxisMoney(worst)}
          </text>
        ) : null}
        <line x1={GUTTER} x2={W - RIGHT} y1={y(0)} y2={y(0)} className="stroke-line-strong" />
        <path d={area} strokeWidth={1.5} vectorEffect="non-scaling-stroke" className="fill-neg/[0.28] stroke-neg" />
      </svg>
    </div>
  )
}
