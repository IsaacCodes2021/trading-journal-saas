import { Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { FilterBar } from '../../components/FilterBar'
import { Button, Card, Chip, DirectionBadge, Pnl, Spinner } from '../../components/ui/primitives'
import type { TradeRow, TradeSortField } from '../../lib/data/model'
import { useFilterOptions, useTrades } from '../../lib/data/queries'
import { formatDuration, formatTradeTime, pluralize } from '../../lib/format'
import type { Filters } from '../../lib/filters'
import { cx } from '../../components/ui/cx'
import { PAGE_SIZES, nextSort, toQuery, type TradeSearch } from './search'

interface Column {
  label: string
  field?: TradeSortField
  right?: boolean
}

const COLUMNS: Column[] = [
  { label: 'Date', field: 'date' },
  { label: 'Account', field: 'account' },
  { label: 'Symbol', field: 'symbol' },
  { label: 'Side', field: 'direction' },
  { label: 'Qty', field: 'qty', right: true },
  { label: 'Entry', right: true },
  { label: 'Exit', right: true },
  { label: 'Hold', field: 'hold', right: true },
  { label: 'Net P&L', field: 'net_pnl', right: true },
  { label: 'Tags' },
  { label: 'Journal' },
]

const cell = 'border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap'

function journalText(trade: TradeRow): string {
  const parts = [
    trade.has_notes ? 'Note' : null,
    trade.screenshot_count > 0 ? pluralize(trade.screenshot_count, 'image') : null,
  ]
  return parts.filter(Boolean).join(' · ') || '—'
}

function SortHeader({
  column,
  sort,
  onSort,
}: {
  column: Column
  sort: string
  onSort: (field: TradeSortField) => void
}) {
  const active = column.field !== undefined && (sort === column.field || sort === `-${column.field}`)
  const ascending = sort === column.field
  const align = column.right ? 'text-right' : 'text-left'
  return (
    <th
      scope="col"
      aria-sort={active ? (ascending ? 'ascending' : 'descending') : column.field ? 'none' : undefined}
      className={cx('border-b border-line px-3.5 py-2.5 text-xs font-medium whitespace-nowrap text-muted', align)}
    >
      {column.field ? (
        <button
          type="button"
          onClick={() => column.field && onSort(column.field)}
          className={cx(
            'cursor-pointer border-0 bg-transparent p-0 font-[inherit] hover:text-ink',
            active ? 'text-ink' : 'text-muted',
          )}
        >
          {column.label}
          {active ? <span aria-hidden="true">{ascending ? ' ↑' : ' ↓'}</span> : null}
        </button>
      ) : (
        column.label
      )}
    </th>
  )
}

function SearchBox({ value, onCommit }: { value: string; onCommit: (text: string) => void }) {
  const [text, setText] = useState(value)
  const [seen, setSeen] = useState(value)
  if (value !== seen) {
    // The URL changed. Follow it (back button, a reset), unless it is only our own commit of what was typed.
    setSeen(value)
    if (value !== text.trim()) setText(value)
  }
  useEffect(() => {
    if (text.trim() === value) return
    const timer = setTimeout(() => onCommit(text.trim()), 300)
    return () => clearTimeout(timer)
  }, [text, value, onCommit])
  return (
    <input
      type="search"
      aria-label="Search trades"
      placeholder="Search symbol, account or tag"
      value={text}
      onChange={(e) => setText(e.target.value)}
      className="h-9 w-80 max-w-full rounded-lg border border-line-strong bg-bg px-3 font-[inherit] text-ink"
    />
  )
}

export function TradeLogPage({
  search,
  timeZone,
  onSearch,
}: {
  search: TradeSearch
  timeZone: string
  onSearch: (next: TradeSearch) => void
}) {
  const trades = useTrades(toQuery(search))
  const options = useFilterOptions()
  const data = trades.data
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / search.per_page))
  const from = total === 0 ? 0 : (search.page - 1) * search.per_page + 1
  const to = Math.min(total, search.page * search.per_page)
  // A change of filter or search starts again from the first page; sorting keeps the place.
  const change = (patch: Partial<TradeSearch>, keepPage = false) =>
    onSearch({ ...search, ...patch, ...(keepPage ? {} : { page: 1 }) })
  const onFilters = (next: Filters) => {
    const { q, sort, per_page } = search
    onSearch({ ...next, ...(q ? { q } : {}), sort, per_page, page: 1 })
  }
  return (
    <>
      <h1 className="sr-only">Trades</h1>
      <FilterBar
        filters={search}
        options={options.data}
        onChange={onFilters}
        summary="Filters are in the URL, so this view can be bookmarked"
      />
      <Card className="p-0" role="region" aria-label="Trade log">
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3.5">
          <SearchBox value={search.q ?? ''} onCommit={(q) => change(q ? { q } : { q: undefined })} />
          <span className="grow" />
          {data ? (
            <div className="text-right text-[13px] text-muted">
              {pluralize(total, 'trade')} &middot; net <Pnl value={data.net_pnl} className="font-semibold" />
            </div>
          ) : null}
        </div>
        {trades.isPending ? <Spinner label="Loading trades" /> : null}
        {trades.isError ? (
          <div role="alert" className="p-6">
            <p className="m-0">Trades could not be loaded.</p>
            <Button className="mt-3" onClick={() => void trades.refetch()}>
              Try again
            </Button>
          </div>
        ) : null}
        {data && total === 0 ? (
          <div role="status" className="p-8 text-center">
            <p className="m-0 text-base font-semibold">No trades match</p>
            <p className="mt-1 text-muted">Change the search or the filters above.</p>
          </div>
        ) : null}
        {data && total > 0 ? (
          <div className="scroll-x">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  {COLUMNS.map((column) => (
                    <SortHeader
                      key={column.label}
                      column={column}
                      sort={search.sort}
                      onSort={(field) => change({ sort: nextSort(search.sort, field) }, true)}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((trade) => (
                  <tr key={trade.id} className="hover:bg-raised">
                    <td className={cx(cell, 'num')}>
                      <Link
                        to="/trades/$tradeId"
                        params={{ tradeId: String(trade.id) }}
                        search={(prev) => prev}
                        className="text-ink no-underline hover:text-accent"
                      >
                        {formatTradeTime(trade.entry_at, timeZone)}
                      </Link>
                    </td>
                    <td className={cell}>{trade.account.name}</td>
                    <td className={cx(cell, 'num')}>{trade.symbol}</td>
                    <td className={cell}>
                      <DirectionBadge direction={trade.direction} />
                    </td>
                    <td className={cx(cell, 'num text-right')}>{trade.total_qty}</td>
                    <td className={cx(cell, 'num text-right')}>{trade.avg_entry}</td>
                    <td className={cx(cell, 'num text-right')}>{trade.avg_exit}</td>
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
        ) : null}
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <span className="text-[13px] text-muted" aria-live="polite">
            {total === 0
              ? '0 trades'
              : `${from.toLocaleString('en-US')} to ${to.toLocaleString('en-US')} of ${total.toLocaleString('en-US')}`}
          </span>
          <span className="grow" />
          <label className="flex items-center gap-2 text-[13px] text-muted">
            Per page
            <select
              value={search.per_page}
              onChange={(e) => change({ per_page: Number(e.target.value) })}
              className="h-9 rounded-lg border border-line bg-raised px-2 font-[inherit] text-ink"
            >
              {PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-center gap-1.5">
            <Button
              icon
              aria-label="Previous page"
              disabled={search.page <= 1}
              onClick={() => change({ page: search.page - 1 }, true)}
            >
              &lsaquo;
            </Button>
            <span className="num min-w-16 text-center text-[13px]">
              {search.page} / {pages}
            </span>
            <Button
              icon
              aria-label="Next page"
              disabled={search.page >= pages}
              onClick={() => change({ page: search.page + 1 }, true)}
            >
              &rsaquo;
            </Button>
          </div>
        </div>
      </Card>
    </>
  )
}
