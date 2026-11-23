import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { Modal } from '../../components/ui/Modal'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { FILLS_DISCLAIMER } from '../../lib/copyTrading'
import type { AccountRef, TradeDetail } from '../../lib/data/model'
import { useDashboard } from '../../lib/data/queries'
import type { Filters } from '../../lib/filters'
import { EquityCard, Tiles } from '../dashboard/DashboardPage'

/** One follower's numbers, all time: the dashboard's own tiles and equity chart, scoped to that account. */
function FollowerAnalytics({ account, onClose }: { account: AccountRef; onClose: () => void }) {
  const filters: Filters = { preset: 'all', account_ids: [account.id], show_archived: true }
  const dashboard = useDashboard(filters)
  return (
    <Modal label={`${account.name} analytics`} onClose={onClose} className="w-[min(960px,calc(100vw-32px))]">
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="m-0 text-base font-semibold">{account.name}</h2>
          <span className="text-[13px] text-muted">all time, copies included &middot; {account.firm}</span>
          <span className="grow" />
          <Button onClick={onClose}>Close</Button>
        </div>
        <p className="m-0 rounded-md bg-raised px-3 py-2 text-[13px] text-muted">Estimated. {FILLS_DISCLAIMER}</p>
        {dashboard.isPending ? <Spinner label="Loading the account's numbers" /> : null}
        {dashboard.isError ? (
          <p role="alert" className="m-0">
            The numbers could not be loaded.
          </p>
        ) : null}
        {dashboard.data ? (
          dashboard.data.summary.trade_count === 0 ? (
            <p className="m-0 text-muted">No trades for this account yet.</p>
          ) : (
            <>
              <Tiles data={dashboard.data} />
              <EquityCard data={dashboard.data} />
            </>
          )
        ) : null}
        <Link to="/" search={filters} className="self-start">
          Open the dashboard for this account
        </Link>
      </div>
    </Modal>
  )
}

/** The follower accounts a copied trade went to. Each opens a short view of that account's own numbers. */
export function CopiedToCard({ trade }: { trade: TradeDetail }) {
  const [open, setOpen] = useState<AccountRef | null>(null)
  const followers = trade.copied_to ?? []
  if (!trade.copy_group || followers.length === 0) return null
  return (
    <Card className="px-5 py-4" role="region" aria-label="Copied to">
      <h2 className="m-0 text-base font-semibold">Copied to</h2>
      <p className="m-0 mt-1 text-[13px] text-muted">
        {trade.copy_group.name}, led by {trade.account.name}. The note, tags and screenshots here are the whole
        group&rsquo;s.
      </p>
      <ul className="m-0 mt-3 flex list-none flex-col gap-1 p-0">
        {followers.map((account) => (
          <li key={account.id}>
            <button
              type="button"
              className="w-full rounded px-2 py-1.5 text-left hover:bg-raised"
              onClick={() => setOpen(account)}
            >
              {account.name} <span className="text-[13px] text-muted">&middot; {account.firm}</span>
            </button>
          </li>
        ))}
      </ul>
      {open ? <FollowerAnalytics account={open} onClose={() => setOpen(null)} /> : null}
    </Card>
  )
}
