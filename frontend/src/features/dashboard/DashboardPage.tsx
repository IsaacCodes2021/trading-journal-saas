import { useId, useState } from 'react'
import { DailyBars } from '../../components/charts/DailyBars'
import { DrawdownChart, EquityChart } from '../../components/charts/EquityCharts'
import { ScoreRadar } from '../../components/charts/ScoreRadar'
import { FilterBar } from '../../components/FilterBar'
import { Button, Card, Pnl, Segmented, Spinner, StatTile } from '../../components/ui/primitives'
import type { DashboardData, PerformanceScore } from '../../lib/data/model'
import { useDashboard } from '../../lib/data/queries'
import { useFilterOptions } from '../../lib/data/queries'
import type { Filters } from '../../lib/filters'
import { formatAmount, formatDay, formatPercent, formatPnl, formatRatio, pluralize } from '../../lib/format'
import { toChartNumber } from '../../lib/money'

function SectionTitle({ children, note }: { children: string; note?: string }) {
  return (
    <>
      <h2 className="m-0 text-base font-semibold">{children}</h2>
      {note ? <span className="text-[13px] text-muted">{note}</span> : null}
    </>
  )
}

function Tiles({ data }: { data: DashboardData }) {
  const s = data.summary
  return (
    <section aria-label="Key statistics" className="flex flex-wrap gap-3">
      <StatTile label="Net P&L" note={`after ${formatAmount(s.fees)} fees`}>
        <Pnl value={s.net_pnl} />
      </StatTile>
      <StatTile label="Win rate" note={`${s.win_count} W · ${s.loss_count} L`}>
        {s.win_rate === null ? '—' : formatPercent(s.win_rate)}
      </StatTile>
      <StatTile label="Profit factor" note="gross win / gross loss">
        {formatRatio(s.profit_factor)}
      </StatTile>
      <StatTile
        label="Avg win / avg loss"
        note={
          s.avg_win !== null && s.avg_loss !== null ? (
            <>
              <Pnl value={s.avg_win} /> / <Pnl value={s.avg_loss} />
            </>
          ) : undefined
        }
      >
        {formatRatio(s.win_loss_ratio)}
      </StatTile>
      <StatTile label="Expectancy" note="per trade">
        {s.expectancy === null ? '—' : <Pnl value={s.expectancy} />}
      </StatTile>
      <StatTile label="Max drawdown" note="closed-trade basis">
        <Pnl value={s.max_drawdown === '0.00' ? '0' : `-${s.max_drawdown}`} />
      </StatTile>
    </section>
  )
}

function EquityCard({ data }: { data: DashboardData }) {
  const [basis, setBasis] = useState<'trade' | 'day'>('trade')
  const points = basis === 'trade' ? data.equity_by_trade : data.equity_by_day
  return (
    <Card className="min-w-0 flex-[2_1_560px] px-5 py-4" role="region" aria-label="Equity and drawdown">
      <div className="flex flex-wrap items-center gap-3">
        <SectionTitle note="cumulative net, closed trades">Equity &amp; drawdown</SectionTitle>
        <span className="grow" />
        <Segmented
          label="Chart basis"
          value={basis}
          onChange={setBasis}
          options={[
            { value: 'trade', label: 'By trade' },
            { value: 'day', label: 'By day' },
          ]}
        />
      </div>
      <EquityChart points={points} unit={basis} />
      <DrawdownChart points={points} />
    </Card>
  )
}

function ScoreCard({ score }: { score: PerformanceScore | null }) {
  const [explain, setExplain] = useState(false)
  const panel = useId()
  return (
    <Card className="min-w-0 flex-[1_1_320px] px-5 py-4" role="region" aria-label="Trader score">
      <div className="flex items-baseline gap-2.5">
        <h2 className="m-0 text-base font-semibold">Trader score</h2>
        {score ? (
          <>
            <span className="num text-[28px] font-semibold text-accent">{Math.round(score.overall)}</span>
            <span className="text-muted">/ 100</span>
          </>
        ) : null}
      </div>
      {score ? (
        <>
          <ScoreRadar axes={score.axes} />
          <ul className="m-0 mt-3 flex list-none flex-col gap-1.5 p-0 text-[13px]">
            {score.axes.map((axis) => (
              <li key={axis.name} className="flex justify-between">
                <span className="text-muted">{axis.name}</span>
                <span className="num">{Math.round(axis.score)}</span>
              </li>
            ))}
          </ul>
          <Button
            className="mt-3.5 w-full"
            aria-expanded={explain}
            aria-controls={panel}
            onClick={() => setExplain((v) => !v)}
          >
            How it&rsquo;s scored
          </Button>
          {explain ? (
            <dl id={panel} className="m-0 mt-3 flex flex-col gap-2.5 text-[13px]">
              {score.axes.map((axis) => (
                <div key={axis.name}>
                  <dt className="font-semibold">{axis.name}</dt>
                  <dd className="m-0 text-muted">{axis.rule}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </>
      ) : (
        <p className="mt-3 text-muted">The score appears once there are trades in the range.</p>
      )}
    </Card>
  )
}

function DailyCard({ data }: { data: DashboardData }) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const first = data.daily[0]
  const last = data.daily[data.daily.length - 1]
  const nets = data.daily.map((d) => d.net_pnl)
  return (
    <Card className="min-w-0 flex-[2_1_560px] px-5 py-4" role="region" aria-label="Daily P&L">
      <div className="flex flex-wrap items-center gap-3">
        <SectionTitle note={`last ${pluralize(data.daily.length, 'trading day')}`}>Daily P&amp;L</SectionTitle>
        <span className="grow" />
        <Segmented
          label="View"
          value={view}
          onChange={setView}
          options={[
            { value: 'chart', label: 'Chart' },
            { value: 'table', label: 'Table' },
          ]}
        />
      </div>
      {view === 'chart' ? (
        <>
          <DailyBars days={data.daily} />
          {first && last ? (
            <div className="num mt-2 flex justify-between text-xs text-muted">
              <span>{formatDay(first.day)}</span>
              <span>best day {formatPnl(nets.reduce((a, b) => (toChartNumber(b) > toChartNumber(a) ? b : a)))}</span>
              <span>worst day {formatPnl(nets.reduce((a, b) => (toChartNumber(b) < toChartNumber(a) ? b : a)))}</span>
              <span>{formatDay(last.day)}</span>
            </div>
          ) : null}
        </>
      ) : (
        <div className="scroll-x mt-3 max-h-[320px] overflow-y-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="border-b border-line px-3.5 py-2.5 text-left text-xs font-medium text-muted">Day</th>
                <th className="border-b border-line px-3.5 py-2.5 text-right text-xs font-medium text-muted">Trades</th>
                <th className="border-b border-line px-3.5 py-2.5 text-right text-xs font-medium text-muted">
                  Net P&amp;L
                </th>
              </tr>
            </thead>
            <tbody>
              {[...data.daily].reverse().map((d) => (
                <tr key={d.day}>
                  <td className="num border-b border-line-soft px-3.5 py-2.5">{formatDay(d.day)}</td>
                  <td className="num border-b border-line-soft px-3.5 py-2.5 text-right">{d.trade_count}</td>
                  <td className="border-b border-line-soft px-3.5 py-2.5 text-right">
                    <Pnl value={d.net_pnl} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function AccountsCard({ data }: { data: DashboardData }) {
  return (
    <Card className="min-w-0 flex-[1_1_320px] pt-4 pb-2" role="region" aria-label="By account">
      <h2 className="m-0 mb-2 px-5 text-base font-semibold">By account</h2>
      <div className="scroll-x">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="border-b border-line px-3.5 py-2.5 text-left text-xs font-medium whitespace-nowrap text-muted">
                Account
              </th>
              <th className="border-b border-line px-3.5 py-2.5 text-right text-xs font-medium whitespace-nowrap text-muted">
                Trades
              </th>
              <th className="border-b border-line px-3.5 py-2.5 text-right text-xs font-medium whitespace-nowrap text-muted">
                Net P&amp;L
              </th>
            </tr>
          </thead>
          <tbody>
            {data.accounts.map((row) => (
              <tr key={row.account.id}>
                <td className="border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap">
                  {row.account.name}
                  <div className="text-xs text-muted">
                    {row.account.account_type === 'funded' ? 'Funded' : 'Evaluation'}
                  </div>
                </td>
                <td className="num border-b border-line-soft px-3.5 py-2.5 text-right">{row.trade_count}</td>
                <td className="border-b border-line-soft px-3.5 py-2.5 text-right whitespace-nowrap">
                  <Pnl value={row.net_pnl} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export function DashboardPage({ filters, onFilters }: { filters: Filters; onFilters: (next: Filters) => void }) {
  const dashboard = useDashboard(filters)
  const options = useFilterOptions()
  const data = dashboard.data
  const range = data?.range
  const summary = data
    ? `${pluralize(data.summary.trade_count, 'trade')} · ${pluralize(data.summary.account_count, 'account')}${
        range ? ` · ${formatDay(range.start)} to ${formatDay(range.end)}` : ''
      }`
    : undefined
  return (
    <>
      <h1 className="sr-only">Dashboard</h1>
      <FilterBar filters={filters} options={options.data} onChange={onFilters} summary={summary} />
      {dashboard.isPending ? <Spinner label="Loading the dashboard" /> : null}
      {dashboard.isError ? (
        <Card className="p-6" role="alert">
          <p className="m-0">The dashboard could not be loaded.</p>
          <Button className="mt-3" onClick={() => void dashboard.refetch()}>
            Try again
          </Button>
        </Card>
      ) : null}
      {data && data.summary.trade_count === 0 ? (
        <Card className="p-8 text-center" role="status">
          <p className="m-0 text-base font-semibold">No trades match these filters</p>
          <p className="mt-1 text-muted">Widen the date range or clear a filter to see results.</p>
          <Button className="mt-4" onClick={() => onFilters({ preset: 'all' })}>
            Show everything
          </Button>
        </Card>
      ) : null}
      {data && data.summary.trade_count > 0 ? (
        <>
          <Tiles data={data} />
          <div className="flex flex-wrap gap-4">
            <EquityCard data={data} />
            <ScoreCard score={data.score} />
          </div>
          <div className="flex flex-wrap gap-4">
            <DailyCard data={data} />
            <AccountsCard data={data} />
          </div>
        </>
      ) : null}
    </>
  )
}
