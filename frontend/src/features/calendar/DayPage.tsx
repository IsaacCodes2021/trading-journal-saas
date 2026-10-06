import { Link } from '@tanstack/react-router'
import { EquityChart } from '../../components/charts/EquityCharts'
import { FilterBar } from '../../components/FilterBar'
import { Button, Card, Chip, DirectionBadge, Pnl, Spinner, StatTile } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import { FIRST_DAY, LAST_DAY, dayLabel, monthOf, shiftDay } from '../../lib/calendar'
import { useDay, useDayNote, useFilterOptions, useSaveDayNote } from '../../lib/data/queries'
import type { TradeRow } from '../../lib/data/model'
import { hasNarrowingFilters, type Filters } from '../../lib/filters'
import { formatAmount, formatClock, formatDuration, formatPercent, pluralize } from '../../lib/format'
import { NoteEditorCard } from '../trade-detail/NotesCard'

const cell = 'border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap'

function journalText(trade: TradeRow): string {
  const parts = [
    trade.has_notes ? 'Note' : null,
    trade.screenshot_count > 0 ? pluralize(trade.screenshot_count, 'image') : null,
  ]
  return parts.filter(Boolean).join(' · ') || '—'
}

/** The note for the day. Loaded on its own, so the trades are not held up by the editor; keyed by day, so each day starts
 * with its own text. */
function DayNote({ day }: { day: string }) {
  const note = useDayNote(day)
  const save = useSaveDayNote(day)
  if (note.isPending) return <Spinner label="Loading the note" />
  if (note.isError) {
    return (
      <Card className="p-6" role="alert">
        <p className="m-0">The note for this day could not be loaded.</p>
        <Button className="mt-3" onClick={() => void note.refetch()}>
          Try again
        </Button>
      </Card>
    )
  }
  return <NoteEditorCard key={day} title="Day note" html={note.data} saver={save} />
}

function DayTrades({ trades, total, timeZone }: { trades: TradeRow[]; total: number; timeZone: string }) {
  return (
    <Card className="p-0" role="region" aria-label="Trades of the day">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3.5">
        <h2 className="m-0 text-base font-semibold">Trades</h2>
        <span className="grow" />
        <span className="text-[13px] text-muted">
          {total > trades.length
            ? `The first ${pluralize(trades.length, 'trade')} of ${total.toLocaleString('en-US')}`
            : pluralize(total, 'trade')}
        </span>
      </div>
      <div className="scroll-x">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              {['Time', 'Account', 'Symbol', 'Side', 'Qty', 'Hold', 'Net P&L', 'Tags', 'Journal'].map(
                (label, index) => (
                  <th
                    key={label}
                    scope="col"
                    className={cx(
                      'border-b border-line px-3.5 py-2.5 text-xs font-medium whitespace-nowrap text-muted',
                      [4, 5, 6].includes(index) ? 'text-right' : 'text-left',
                    )}
                  >
                    {label}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {trades.map((trade) => (
              <tr key={trade.id} className="hover:bg-raised">
                <td className={cx(cell, 'num')}>
                  <Link
                    to="/trades/$tradeId"
                    params={{ tradeId: String(trade.id) }}
                    search={(prev) => prev}
                    className="-my-2 inline-block py-2 text-ink no-underline hover:text-accent"
                  >
                    {formatClock(trade.entry_at, timeZone)}
                  </Link>
                </td>
                <td className={cell}>{trade.account.name}</td>
                <td className={cx(cell, 'num')}>{trade.symbol}</td>
                <td className={cell}>
                  <DirectionBadge direction={trade.direction} />
                </td>
                <td className={cx(cell, 'num text-right')}>{trade.total_qty}</td>
                <td className={cx(cell, 'num text-right')}>{formatDuration(trade.hold_seconds)}</td>
                <td className={cx(cell, 'text-right')}>
                  <Pnl value={trade.net_pnl} />
                </td>
                <td className={cell}>
                  {trade.tags.length > 0 ? (
                    <span className="flex gap-1">
                      {trade.tags.map((tag) => (
                        <Chip key={tag.id} color={tag.color}>
                          {tag.name}
                        </Chip>
                      ))}
                    </span>
                  ) : (
                    <span className="text-muted">&mdash;</span>
                  )}
                </td>
                <td className={cx(cell, 'text-muted')}>{journalText(trade)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function DayView({
  day,
  filters,
  timeZone,
  onFilters,
}: {
  day: string
  filters: Filters
  timeZone: string
  onFilters: (next: Filters) => void
}) {
  const data = useDay(day, filters)
  const options = useFilterOptions()
  const result = data.data
  const month = monthOf(day)
  const earlier = day <= FIRST_DAY
  const later = day >= LAST_DAY
  return (
    <>
      <FilterBar
        filters={filters}
        options={options.data}
        onChange={onFilters}
        showRange={false}
        summary="The day is the date range"
      />
      <Card className="flex flex-wrap items-center gap-2 px-4 py-3" role="navigation" aria-label="Day">
        <Link to="/calendar" search={{ ...filters, month }} className="-my-2 mr-2 inline-block py-2 text-[13px]">
          &lsaquo; Calendar
        </Link>
        {earlier ? (
          <Button icon aria-label="Previous day" disabled>
            &lsaquo;
          </Button>
        ) : (
          <Link
            to="/day/$day"
            params={{ day: shiftDay(day, -1) }}
            search={filters}
            aria-label="Previous day"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-raised no-underline"
          >
            &lsaquo;
          </Link>
        )}
        <h2 className="m-0 min-w-[240px] text-center text-base font-semibold" aria-live="polite">
          {dayLabel(day)}
        </h2>
        {later ? (
          <Button icon aria-label="Next day" disabled>
            &rsaquo;
          </Button>
        ) : (
          <Link
            to="/day/$day"
            params={{ day: shiftDay(day, 1) }}
            search={filters}
            aria-label="Next day"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-raised no-underline"
          >
            &rsaquo;
          </Link>
        )}
      </Card>
      {data.isPending ? <Spinner label="Loading the day" /> : null}
      {data.isError ? (
        <Card className="p-6" role="alert">
          <p className="m-0">This day could not be loaded.</p>
          <Button className="mt-3" onClick={() => void data.refetch()}>
            Try again
          </Button>
        </Card>
      ) : null}
      {result && result.trades.length === 0 ? (
        <p role="status" className="m-0 text-muted">
          No trades on this day{hasNarrowingFilters(filters) ? ' with these filters' : ''}. You can still write a note.
        </p>
      ) : null}
      {result && result.trades.length > 0 ? (
        <>
          <section aria-label="The day at a glance" className="flex flex-wrap gap-3">
            <StatTile label="Net P&L" note={`after ${formatAmount(result.summary.fees)} fees`}>
              <Pnl value={result.summary.net_pnl} />
            </StatTile>
            <StatTile label="Trades" note={`${result.summary.win_count} W · ${result.summary.loss_count} L`}>
              {result.summary.trade_count}
            </StatTile>
            <StatTile label="Win rate">
              {result.summary.win_rate === null ? '—' : formatPercent(result.summary.win_rate)}
            </StatTile>
            <StatTile label="Largest win">
              {result.summary.largest_win ? <Pnl value={result.summary.largest_win} /> : '—'}
            </StatTile>
            <StatTile label="Largest loss">
              {result.summary.largest_loss ? <Pnl value={result.summary.largest_loss} /> : '—'}
            </StatTile>
          </section>
          {result.trades.length > 1 ? (
            <Card className="px-5 py-4" role="region" aria-label="The day's equity">
              <h2 className="m-0 text-base font-semibold">Equity through the day</h2>
              <EquityChart points={result.equity_by_trade} unit="trade" timeZone={timeZone} />
            </Card>
          ) : null}
          <DayTrades trades={result.trades} total={result.total} timeZone={timeZone} />
        </>
      ) : null}
      <DayNote day={day} />
    </>
  )
}

/** One trading day: its result, its trades, and a note for it that saves by itself. `day` is null for an address that is
 * not a real day. */
export function DayPage({
  day,
  filters,
  timeZone,
  onFilters,
}: {
  day: string | null
  filters: Filters
  timeZone: string
  onFilters: (next: Filters) => void
}) {
  return (
    <>
      <h1 className="sr-only">Day</h1>
      {day === null ? (
        <Card className="p-8 text-center" role="alert">
          <p className="m-0 text-base font-semibold">That is not a day this app has</p>
          <p className="mt-1 text-muted">A day looks like 2026-09-30, between 2000 and 2100.</p>
          <Link to="/calendar" search={filters} className="mt-3 inline-block">
            Back to the calendar
          </Link>
        </Card>
      ) : (
        <DayView key={day} day={day} filters={filters} timeZone={timeZone} onFilters={onFilters} />
      )}
    </>
  )
}
