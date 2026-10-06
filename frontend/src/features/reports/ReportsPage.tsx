import { Link } from '@tanstack/react-router'
import { FilterBar } from '../../components/FilterBar'
import { cx } from '../../components/ui/cx'
import { Button, Card, Pnl, Spinner } from '../../components/ui/primitives'
import { REPORT_BYS, type ReportBy, type ReportData, type ReportGroup, type SummaryStats } from '../../lib/data/model'
import { useFilterOptions, useReport } from '../../lib/data/queries'
import type { Filters } from '../../lib/filters'
import { formatPercent, formatRatio, pluralize } from '../../lib/format'
import { REPORT_KINDS, barShare, largestResult } from '../../lib/reports'

/** The page's address: what to break the trades down by (left out for the first choice, contract) and the filters. */
export type ReportsSearch = Filters & { by?: ReportBy }

const th = 'border-b border-line px-3.5 py-2.5 text-xs font-medium whitespace-nowrap text-muted'
const td = 'border-b border-line-soft px-3.5 py-2.5'

function Money({ value }: { value: string | null }) {
  return value === null ? <span className="text-muted">—</span> : <Pnl value={value} />
}

/** A bar as long as the group's result is next to the biggest one in the report: green to the right of nothing, red the same. */
function ResultBar({ net, largest }: { net: string; largest: string }) {
  const share = barShare(net, largest)
  if (share === 0) return <span aria-hidden="true" className="h-2 w-[110px]" />
  return (
    <span aria-hidden="true" className="flex h-2 w-[110px] shrink-0 overflow-hidden rounded-sm bg-raised">
      <span
        className={cx('h-full', Number(net) > 0 ? 'bg-pos' : 'bg-neg')}
        style={{ width: `${Math.max(2, share * 100)}%` }}
      />
    </span>
  )
}

function Figures({ summary }: { summary: SummaryStats }) {
  return (
    <>
      <td className={cx(td, 'num text-right')}>{summary.trade_count}</td>
      <td className={cx(td, 'num text-right')}>
        {summary.win_rate === null ? <span className="text-muted">—</span> : formatPercent(summary.win_rate)}
      </td>
      <td className={cx(td, 'text-right whitespace-nowrap')}>
        <Money value={summary.expectancy} />
      </td>
      <td className={cx(td, 'num text-right')}>{formatRatio(summary.profit_factor)}</td>
      <td className={cx(td, 'text-right whitespace-nowrap')}>
        <Money value={summary.avg_win} />
      </td>
      <td className={cx(td, 'text-right whitespace-nowrap')}>
        <Money value={summary.avg_loss} />
      </td>
    </>
  )
}

function GroupLabel({ group, by, filters }: { group: ReportGroup; by: ReportBy; filters: Filters }) {
  return (
    <>
      {by === 'symbol' ? (
        <Link
          to="/trades"
          search={{ ...filters, symbol_roots: [group.key] }}
          className="-mx-2 -my-2 inline-block px-2 py-2"
        >
          {group.label}
        </Link>
      ) : (
        group.label
      )}
      {group.detail ? <div className="text-xs text-muted">{group.detail}</div> : null}
    </>
  )
}

function ReportTable({ data, filters }: { data: ReportData; filters: Filters }) {
  const kind = REPORT_KINDS[data.by]
  const largest = largestResult(data.groups)
  return (
    <Card className="p-0" role="region" aria-label={`Results by ${kind.label.toLowerCase()}`}>
      <div className="scroll-x">
        <table className="w-full min-w-[780px] border-collapse">
          <caption className="sr-only">
            The filtered trades by {kind.label.toLowerCase()}. {kind.how}
          </caption>
          <thead>
            <tr>
              <th scope="col" className={cx(th, 'text-left')}>
                {kind.heading}
              </th>
              <th scope="col" className={cx(th, 'text-right')}>
                Trades
              </th>
              <th scope="col" className={cx(th, 'text-right')}>
                Win rate
              </th>
              <th scope="col" className={cx(th, 'text-right')}>
                Expectancy
              </th>
              <th scope="col" className={cx(th, 'text-right')}>
                Profit factor
              </th>
              <th scope="col" className={cx(th, 'text-right')}>
                Avg win
              </th>
              <th scope="col" className={cx(th, 'text-right')}>
                Avg loss
              </th>
              <th scope="col" className={cx(th, 'text-right')}>
                Net P&amp;L
              </th>
            </tr>
          </thead>
          <tbody>
            {data.groups.map((group) => (
              <tr key={group.key} className={cx(group.summary.trade_count === 0 && 'text-muted')}>
                <th scope="row" className={cx(td, 'text-left font-normal whitespace-nowrap')}>
                  <GroupLabel group={group} by={data.by} filters={filters} />
                </th>
                <Figures summary={group.summary} />
                <td className={cx(td, 'text-right whitespace-nowrap')}>
                  <span className="inline-flex items-center justify-end gap-2.5">
                    <ResultBar net={group.summary.net_pnl} largest={largest} />
                    <span className="inline-block min-w-[84px] text-right">
                      {group.summary.trade_count === 0 ? (
                        <span className="text-muted">—</span>
                      ) : (
                        <Pnl value={group.summary.net_pnl} />
                      )}
                    </span>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="font-semibold">
              <th scope="row" className={cx(td, 'text-left whitespace-nowrap')}>
                All trades
              </th>
              <Figures summary={data.summary} />
              <td className={cx(td, 'text-right whitespace-nowrap')}>
                <span className="inline-block min-w-[84px]">
                  <Pnl value={data.summary.net_pnl} />
                </span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  )
}

function ByChoice({ by, onChange }: { by: ReportBy; onChange: (next: ReportBy) => void }) {
  return (
    <div role="group" aria-label="Break down by" className="flex flex-wrap items-center gap-2">
      <span className="text-[13px] text-muted">Break down by</span>
      {REPORT_BYS.map((choice) => (
        <Button
          key={choice}
          variant={choice === by ? 'primary' : 'default'}
          aria-pressed={choice === by}
          onClick={() => onChange(choice)}
        >
          {REPORT_KINDS[choice].label}
        </Button>
      ))}
    </div>
  )
}

/**
 * The filtered trades broken down by contract, weekday, time of day, hold time, tag or account: the same statistics as the
 * dashboard for each group, so a weak weekday, a costly hour or a tag that pays can be seen at a glance. Every choice and
 * filter is in the address, so a view can be bookmarked.
 */
export function ReportsPage({
  search,
  timeZone,
  onSearch,
}: {
  search: ReportsSearch
  timeZone: string
  onSearch: (next: ReportsSearch) => void
}) {
  const { by = 'symbol', ...filters } = search
  const report = useReport(by, filters)
  const options = useFilterOptions()
  const data = report.data
  const kind = REPORT_KINDS[by]
  const summary = data
    ? `${pluralize(data.summary.trade_count, 'trade')} · ${pluralize(data.summary.account_count, 'account')}`
    : undefined
  return (
    <>
      <h1 className="sr-only">Reports</h1>
      <FilterBar
        filters={filters}
        options={options.data}
        onChange={(next) => onSearch({ ...next, by })}
        summary={summary}
      />
      <ByChoice by={by} onChange={(next) => onSearch({ ...filters, by: next })} />
      <p className="m-0 text-[13px] text-muted">
        {kind.how}
        {by === 'hour' ? ` (${timeZone})` : ''}
      </p>
      {report.isPending ? <Spinner label="Loading the report" /> : null}
      {report.isError ? (
        <Card className="p-6" role="alert">
          <p className="m-0">The report could not be loaded.</p>
          <Button className="mt-3" onClick={() => void report.refetch()}>
            Try again
          </Button>
        </Card>
      ) : null}
      {data && data.summary.trade_count === 0 ? (
        <Card className="p-8 text-center" role="status">
          <p className="m-0 text-base font-semibold">No trades match these filters</p>
          <p className="mt-1 text-muted">Widen the date range or clear a filter to see results.</p>
          <Button className="mt-4" onClick={() => onSearch({ preset: 'all', by })}>
            Show everything
          </Button>
        </Card>
      ) : null}
      {data && data.summary.trade_count > 0 ? <ReportTable data={data} filters={filters} /> : null}
    </>
  )
}
