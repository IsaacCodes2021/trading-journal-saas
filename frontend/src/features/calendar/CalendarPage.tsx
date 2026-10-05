import { Link } from '@tanstack/react-router'
import { FilterBar } from '../../components/FilterBar'
import { cx } from '../../components/ui/cx'
import { Button, Card, Pnl, Spinner, StatTile } from '../../components/ui/primitives'
import {
  FIRST_MONTH,
  LAST_MONTH,
  buildWeeks,
  dayLabel,
  extremeDay,
  largestDay,
  monthLabel,
  monthOf,
  shade,
  shiftMonth,
  type CalendarCell,
} from '../../lib/calendar'
import type { CalendarMonth } from '../../lib/data/model'
import { useCalendar, useDashboard, useFilterOptions } from '../../lib/data/queries'
import { hasNarrowingFilters, type Filters } from '../../lib/filters'
import { formatAmount, formatDay, formatPercent, formatPnl, pluralize } from '../../lib/format'

/** The page's address: the month (left out for "the latest month with trades") and the filters. */
export type CalendarSearch = Filters & { month?: string }

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Whole class names, so the stylesheet builder finds them: a day is shaded by how big it is next to the month's biggest.
const SHADES = {
  pos: ['', 'bg-pos/10', 'bg-pos/20', 'bg-pos/35'],
  neg: ['', 'bg-neg/10', 'bg-neg/20', 'bg-neg/35'],
} as const

const th = 'border-b border-line px-2 py-2 text-center text-xs font-medium text-muted'

function currentMonth(): string {
  const now = new Date().toISOString().slice(0, 7)
  return now < FIRST_MONTH ? FIRST_MONTH : now > LAST_MONTH ? LAST_MONTH : now
}

function DayCell({ cell, largest, filters }: { cell: CalendarCell; largest: string; filters: Filters }) {
  const { result, noted } = cell
  const sign = result ? Number(result.net_pnl) : 0
  const tone = sign > 0 ? 'pos' : sign < 0 ? 'neg' : null
  const shading = result && tone ? SHADES[tone][shade(result.net_pnl, largest)] : ''
  const label = [
    dayLabel(cell.day),
    result ? `${formatPnl(result.net_pnl)} from ${pluralize(result.trade_count, 'trade')}` : 'no trades',
    noted ? 'has a note' : null,
  ]
    .filter(Boolean)
    .join(', ')
  return (
    <td className={cx('h-[88px] min-w-[96px] border border-line-soft p-0 align-top', shading)}>
      <Link
        to="/day/$day"
        params={{ day: cell.day }}
        search={filters}
        aria-label={label}
        className="flex h-full min-h-[88px] flex-col gap-1 p-2 text-ink no-underline hover:bg-raised hover:text-ink"
      >
        <span className="flex items-center justify-between text-xs text-muted">
          <span className="num">{cell.number}</span>
          {noted ? <span aria-hidden="true" className="size-[7px] rounded-full bg-accent" /> : null}
        </span>
        {result ? (
          <>
            <Pnl value={result.net_pnl} className="text-[13px] font-semibold" />
            <span className="text-xs text-muted">{pluralize(result.trade_count, 'trade')}</span>
          </>
        ) : null}
      </Link>
    </td>
  )
}

function MonthTiles({ data }: { data: CalendarMonth }) {
  const s = data.summary
  const best = extremeDay(data.daily, 'best')
  const worst = extremeDay(data.daily, 'worst')
  return (
    <section aria-label="The month at a glance" className="flex flex-wrap gap-3">
      <StatTile label="Net P&L" note={`after ${formatAmount(s.fees)} fees`}>
        <Pnl value={s.net_pnl} />
      </StatTile>
      <StatTile label="Trading days" note={`${s.winning_days} up · ${s.losing_days} down`}>
        {s.trading_days}
      </StatTile>
      <StatTile
        label="Trades"
        note={
          s.win_rate === null
            ? undefined
            : `${formatPercent(s.win_rate)} win rate · ${s.win_count} W · ${s.loss_count} L`
        }
      >
        {s.trade_count}
      </StatTile>
      <StatTile label="Best day" note={best ? formatDay(best.day) : undefined}>
        {best ? <Pnl value={best.net_pnl} /> : '—'}
      </StatTile>
      <StatTile label="Worst day" note={worst ? formatDay(worst.day) : undefined}>
        {worst ? <Pnl value={worst.net_pnl} /> : '—'}
      </StatTile>
    </section>
  )
}

function MonthView({
  month,
  latest,
  filters,
  onSearch,
}: {
  month: string
  latest: string | undefined
  filters: Filters
  onSearch: (next: CalendarSearch) => void
}) {
  const calendar = useCalendar(month, filters)
  const options = useFilterOptions()
  const data = calendar.data
  const go = (next: string) => onSearch({ ...filters, month: next })
  const weeks = data ? buildWeeks(month, data.daily, data.noted) : []
  const largest = data ? largestDay(data.daily) : '0.00'
  return (
    <>
      <FilterBar
        filters={filters}
        options={options.data}
        onChange={(next) => onSearch({ ...next, month })}
        showRange={false}
        summary="The month is the date range"
      />
      <Card className="flex flex-wrap items-center gap-2 px-4 py-3" role="navigation" aria-label="Month">
        <Button
          icon
          aria-label="Previous month"
          disabled={month <= FIRST_MONTH}
          onClick={() => go(shiftMonth(month, -1))}
        >
          &lsaquo;
        </Button>
        <h2 className="m-0 min-w-[160px] text-center text-base font-semibold" aria-live="polite">
          {monthLabel(month)}
        </h2>
        <Button icon aria-label="Next month" disabled={month >= LAST_MONTH} onClick={() => go(shiftMonth(month, 1))}>
          &rsaquo;
        </Button>
        <span className="grow" />
        {latest && latest !== month ? <Button onClick={() => go(latest)}>Latest month with trades</Button> : null}
      </Card>
      {calendar.isPending ? <Spinner label="Loading the calendar" /> : null}
      {calendar.isError ? (
        <Card className="p-6" role="alert">
          <p className="m-0">The calendar could not be loaded.</p>
          <Button className="mt-3" onClick={() => void calendar.refetch()}>
            Try again
          </Button>
        </Card>
      ) : null}
      {data ? <MonthTiles data={data} /> : null}
      {data && data.daily.length === 0 ? (
        <p role="status" className="m-0 text-muted">
          No trades in {monthLabel(month)}
          {hasNarrowingFilters(filters) ? ' with these filters' : ''}. You can still open any day to write a note.
        </p>
      ) : null}
      {data ? (
        <Card className="p-0" role="region" aria-label="Calendar">
          <div className="scroll-x">
            <table className="w-full min-w-[760px] table-fixed border-collapse">
              <caption className="sr-only">Trading days in {monthLabel(month)}, with the result of each week</caption>
              <thead>
                <tr>
                  {WEEKDAYS.map((name) => (
                    <th key={name} scope="col" className={th}>
                      {name}
                    </th>
                  ))}
                  <th scope="col" className={cx(th, 'w-[110px]')}>
                    Week
                  </th>
                </tr>
              </thead>
              <tbody>
                {weeks.map((week) => (
                  <tr key={week.cells.find((c) => c)?.day}>
                    {week.cells.map((cell, index) =>
                      cell ? (
                        <DayCell key={cell.day} cell={cell} largest={largest} filters={filters} />
                      ) : (
                        <td key={`blank-${index}`} className="border border-line-soft bg-bg" aria-hidden="true" />
                      ),
                    )}
                    <td className="border border-line-soft px-2 py-2 text-center align-top">
                      {week.net === null ? (
                        <span className="text-muted">—</span>
                      ) : (
                        <>
                          <Pnl value={week.net} className="text-[13px] font-semibold" />
                          <div className="text-xs text-muted">{pluralize(week.trades, 'trade')}</div>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
    </>
  )
}

/**
 * A month of trading days: each day's net result and trade count, shaded by how big it was, with a total for every week.
 * Every day opens the day page, where the day's note is. Without `month` in the address it shows the latest month that
 * has trades (this month when there are none).
 */
export function CalendarPage({
  search,
  onSearch,
}: {
  search: CalendarSearch
  onSearch: (next: CalendarSearch) => void
}) {
  const { month, ...filters } = search
  const everything = useDashboard({ ...filters, preset: 'all' })
  const latest = everything.data?.range ? monthOf(everything.data.range.end) : undefined
  return (
    <>
      <h1 className="sr-only">Calendar</h1>
      {month === undefined && everything.isPending ? (
        <Spinner label="Loading the calendar" />
      ) : (
        <MonthView month={month ?? latest ?? currentMonth()} latest={latest} filters={filters} onSearch={onSearch} />
      )}
    </>
  )
}
