import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { Button, Card, DirectionBadge, Pnl, Spinner } from '../../components/ui/primitives'
import type { TagCatalog, TradeDetail } from '../../lib/data/model'
import { useNeighbors, useTagCatalog, useTrade } from '../../lib/data/queries'
import {
  formatAmount,
  formatDay,
  formatDuration,
  formatPnl,
  formatPoints,
  formatTradeTime,
  pluralize,
} from '../../lib/format'
import { toQuery, type TradeSearch } from '../trades/search'
import { FillPairsCard } from './FillPairsCard'
import { NotesCard } from './NotesCard'
import { ScreenshotsCard } from './ScreenshotsCard'
import { TagsCard } from './TagsCard'

function Figure({ label, children, big = false }: { label: string; children: ReactNode; big?: boolean }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className={big ? 'num text-[28px] font-semibold' : 'num mt-1 text-xl'}>{children}</div>
    </div>
  )
}

function Summary({ trade }: { trade: TradeDetail }) {
  return (
    <Card className="flex flex-wrap gap-x-10 gap-y-6 px-5 py-4" role="region" aria-label="Result">
      <Figure label="Net P&L" big>
        <Pnl value={trade.net_pnl} />
      </Figure>
      <Figure label="Gross">{formatPnl(trade.gross_pnl)}</Figure>
      <Figure label="Fees">{formatAmount(trade.fees)}</Figure>
      <Figure label="Points">{formatPoints(trade.points)}</Figure>
      <Figure label="Hold time">{formatDuration(trade.hold_seconds)}</Figure>
      <Figure label="Trading day">{formatDay(trade.trade_day)}</Figure>
    </Card>
  )
}

/** What is filled in on this trade, so a gap in the journal is visible at a glance. */
function JournalStatus({ trade, catalog }: { trade: TradeDetail; catalog: TagCatalog | undefined }) {
  const setupGroup = catalog?.groups.find((g) => g.name === 'Setup')
  const hasSetup = setupGroup ? trade.tags.some((t) => t.group_id === setupGroup.id) : null
  const rows: [string, string][] = [
    ['Notes', trade.has_notes ? 'Written' : 'Empty'],
    ['Setup tag', hasSetup === null ? '…' : hasSetup ? 'Set' : 'Not set'],
    ['Screenshots', trade.screenshots.length > 0 ? pluralize(trade.screenshots.length, 'image') : 'None'],
  ]
  return (
    <Card className="px-5 py-4" role="region" aria-label="Journal status">
      <h2 className="m-0 mb-2.5 text-[13px] font-semibold tracking-wide text-muted uppercase">Journal status</h2>
      <ul className="m-0 flex list-none flex-col gap-2 p-0 text-[13px]">
        {rows.map(([label, value]) => (
          <li key={label} className="flex justify-between">
            <span className="text-muted">{label}</span>
            <span>{value}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function NeighborLink({ id, children, label }: { id: number | null | undefined; children: string; label: string }) {
  if (id === null || id === undefined) {
    return (
      <Button disabled aria-label={label}>
        {children}
      </Button>
    )
  }
  return (
    <Link
      to="/trades/$tradeId"
      params={{ tradeId: String(id) }}
      search={(prev) => prev}
      aria-label={label}
      className="inline-flex h-9 items-center rounded-lg border border-line bg-raised px-3 text-ink no-underline hover:border-line-strong hover:text-ink"
    >
      {children}
    </Link>
  )
}

function Missing() {
  return (
    <Card className="mx-auto mt-8 max-w-xl p-8 text-center" role="status">
      <h1 className="m-0 text-xl font-semibold">Trade not found</h1>
      <p className="mt-2 text-muted">It may have been removed, or the link is wrong.</p>
      <Link to="/trades" search={(prev) => prev} className="mt-4 inline-block">
        Back to the trades
      </Link>
    </Card>
  )
}

/** One trade: its result and fill pairs, the journal note, tags and screenshots. The arrows follow the list's order. */
export function TradeDetailPage({
  tradeId,
  search,
  timeZone,
}: {
  tradeId: number | null // null when the address named no valid trade
  search: TradeSearch
  timeZone: string
}) {
  const valid = tradeId !== null
  const id = tradeId ?? 0
  const trade = useTrade(id)
  const catalog = useTagCatalog()
  const neighbors = useNeighbors(id, { ...toQuery(search), page: 1, per_page: 1 })

  if (!valid) return <Missing />
  if (trade.isPending) return <Spinner label="Loading the trade" />
  if (trade.isError) {
    return (
      <Card className="p-6" role="alert">
        <p className="m-0">The trade could not be loaded.</p>
        <Button className="mt-3" onClick={() => void trade.refetch()}>
          Try again
        </Button>
      </Card>
    )
  }
  const data = trade.data
  if (data === null) return <Missing />

  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/trades" search={(prev) => prev} className="-my-2 inline-block py-2 no-underline">
          &larr; Trades
        </Link>
        <span className="text-muted">/</span>
        <h1 className="m-0 flex flex-wrap items-center gap-2.5 text-xl font-semibold">
          <span className="num">{data.symbol}</span>
          <DirectionBadge direction={data.direction} />
          <span className="text-sm font-normal text-muted">
            {formatTradeTime(data.entry_at, timeZone)} &middot; {data.account.name}
          </span>
        </h1>
        <span className="grow" />
        <NeighborLink id={neighbors.data?.previous} label="Previous trade">
          ← Previous
        </NeighborLink>
        <NeighborLink id={neighbors.data?.next} label="Next trade">
          Next →
        </NeighborLink>
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <div className="flex min-w-0 flex-[2_1_620px] flex-col gap-4">
          <Summary trade={data} />
          <FillPairsCard trade={data} timeZone={timeZone} />
          <NotesCard key={data.id} trade={data} />
        </div>
        <div className="flex min-w-0 flex-[1_1_340px] flex-col gap-4">
          <TagsCard trade={data} catalog={catalog.data} />
          <ScreenshotsCard key={data.id} trade={data} />
          <JournalStatus trade={data} catalog={catalog.data} />
        </div>
      </div>
    </>
  )
}
