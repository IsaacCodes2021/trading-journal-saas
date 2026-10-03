import { useState } from 'react'
import { Button, Spinner } from '../../components/ui/primitives'
import type { AccountOverview } from '../../lib/data/model'
import { useAccountOverviews } from '../../lib/data/queries'
import { AccountList } from './AccountList'
import { AccountPanel } from './AccountPanel'
import type { Tab } from './tabs'
import { AddAccountDialog, ArchiveDialog } from './AccountDialogs'
import { BusinessCard } from './BusinessCard'
import { SectionHeader } from '../settings/SettingsLayout'

type Dialog =
  | { kind: 'add'; start?: Parameters<typeof AddAccountDialog>[0]['start'] }
  | { kind: 'archive'; overview: AccountOverview }
  | null

/** Prop accounts: the list, the business totals, and the picked account's details, fees, rules and ledger. */
export function AccountsPage({
  selected,
  tab,
  onSelect,
}: {
  selected: number | null
  tab: Tab
  onSelect: (id: number | null, tab?: Tab) => void
}) {
  const overviews = useAccountOverviews()
  const [dialog, setDialog] = useState<Dialog>(null)
  const data = overviews.data
  const current = data?.find((o) => o.account.id === selected)

  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <SectionHeader title="Prop accounts">Evaluations and funded accounts across your firms.</SectionHeader>
        <span className="grow" />
        <Button variant="primary" onClick={() => setDialog({ kind: 'add' })}>
          + Add account
        </Button>
      </div>
      {overviews.isPending ? <Spinner label="Loading your accounts" /> : null}
      {overviews.isError ? (
        <p role="alert" className="m-0">
          Your accounts could not be loaded.
        </p>
      ) : null}
      {data ? (
        <div className="flex flex-wrap items-start gap-4">
          <div className="flex min-w-0 flex-[3_1_480px] flex-col gap-4">
            <AccountList overviews={data} selected={selected} onSelect={(id) => onSelect(id)} />
            <BusinessCard />
          </div>
          {current ? (
            <AccountPanel
              overview={current}
              tab={tab}
              onTab={(next) => onSelect(current.account.id, next)}
              onArchive={() => setDialog({ kind: 'archive', overview: current })}
            />
          ) : (
            <p className="m-0 min-w-0 flex-[2_1_340px] rounded-[10px] border border-dashed border-line-strong p-6 text-center text-muted">
              Choose an account to see and change its details, fees, rules and ledger.
            </p>
          )}
        </div>
      ) : null}

      {dialog?.kind === 'add' ? (
        <AddAccountDialog
          {...(dialog.start ? { start: dialog.start } : {})}
          onClose={() => setDialog(null)}
          onCreated={(account) => {
            setDialog(null)
            onSelect(account.id, 'details')
          }}
        />
      ) : null}
      {dialog?.kind === 'archive' ? (
        <ArchiveDialog
          account={dialog.overview.account}
          lastTradingDay={dialog.overview.progress.last_trading_day}
          onClose={() => setDialog(null)}
          onArchived={(setUpFunded) => {
            const archived = dialog.overview.account
            if (setUpFunded) {
              setDialog({
                kind: 'add',
                start: {
                  name: `${archived.name} funded`,
                  firm: archived.firm,
                  type: 'funded',
                  size: archived.account_size?.replace(/\.00$/, '') ?? '',
                  balance: archived.starting_balance.replace(/\.00$/, ''),
                },
              })
            } else {
              setDialog(null)
            }
            onSelect(null)
          }}
        />
      ) : null}
    </>
  )
}
