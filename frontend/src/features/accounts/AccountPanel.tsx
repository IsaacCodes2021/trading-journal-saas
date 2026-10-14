import { useId, useRef, useState, type KeyboardEvent } from 'react'
import { Button, Card } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { AccountOverview } from '../../lib/data/model'
import { useRestoreAccount, useUpdateAccount } from '../../lib/data/queries'
import { formatDay } from '../../lib/format'
import { OutcomePill, StagePill } from './AccountList'
import { AccountForm } from './AccountDialogs'
import { FeesTab } from './FeesTab'
import { FirmRulesTab } from './FirmRulesTab'
import { LedgerTab } from './LedgerTab'
import { PayoutsTab } from './PayoutsTab'
import { RulesTab } from './RulesTab'
import { TABS, type Tab } from './tabs'

const LABELS: Record<Tab, string> = {
  details: 'Details',
  fees: 'Fees',
  rules: 'Rules',
  'firm-rules': 'Firm rules',
  payouts: 'Payouts',
  ledger: 'Ledger',
}

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'That did not work. Nothing was changed.'
}

function DetailsTab({ overview, onSetUpFunded }: { overview: AccountOverview; onSetUpFunded: () => void }) {
  const { account } = overview
  const update = useUpdateAccount(account.id)
  const restore = useRestoreAccount(account.id)
  const [saved, setSaved] = useState(false)
  return (
    <div className="flex flex-col gap-3.5">
      {account.pass_prompt_dismissed_at && account.account_type === 'evaluation' && account.outcome === null ? (
        <div
          className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2.5 text-[13px]"
          role="note"
        >
          <span className="text-muted">Passed this evaluation? Archive it and set up the funded account.</span>
          <span className="grow" />
          <Button onClick={onSetUpFunded}>Set up the funded account</Button>
        </div>
      ) : null}
      {account.outcome ? (
        <div
          className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2.5 text-[13px]"
          role="note"
        >
          <OutcomePill outcome={account.outcome} />
          <span className="text-muted">
            Archived{account.ended_on ? `, ended ${formatDay(account.ended_on)}` : ''}.
            {account.archive_note ? ` ${account.archive_note}` : ''}
          </span>
          <span className="grow" />
          <Button disabled={restore.isPending} onClick={() => restore.mutate(undefined)}>
            Restore account
          </Button>
        </div>
      ) : null}
      <AccountForm
        key={`${account.id}:${account.name}:${account.firm}:${account.account_type}:${account.account_size}:${account.starting_balance}:${account.purchase_date}:${account.phase}`}
        initial={{
          name: account.name,
          firm: account.firm,
          type: account.account_type,
          size: account.account_size?.replace(/\.00$/, '') ?? '',
          balance: account.starting_balance.replace(/\.00$/, ''),
          purchaseDate: account.purchase_date ?? '',
          live: account.phase === 'live',
        }}
        submitLabel="Save details"
        pending={update.isPending}
        error={update.isError ? messageOf(update.error) : null}
        onSubmit={(input) => update.mutate(input, { onSuccess: () => setSaved(true) })}
      />
      {saved && !update.isPending && !update.isError ? (
        <span role="status" className="text-[13px] text-pos">
          Saved
        </span>
      ) : null}
      {update.isError ? null : (
        <p className="m-0 text-xs text-muted">
          Changing the stage (evaluation or funded) reclassifies all of this account&rsquo;s trades at once.
        </p>
      )}
    </div>
  )
}

/** The account you picked: its details, fees, rules, firm rules, payouts and ledger, as tabs. `suggest` starts the firm
 * rules search (the funded account that follows a passed evaluation). */
export function AccountPanel({
  overview,
  tab,
  onTab,
  onArchive,
  onSetUpFunded = () => undefined,
  suggest = '',
}: {
  overview: AccountOverview
  tab: Tab
  onTab: (next: Tab) => void
  onArchive: () => void
  onSetUpFunded?: () => void
  suggest?: string
}) {
  const { account } = overview
  const id = useId()
  const buttons = useRef<Record<string, HTMLButtonElement | null>>({})

  const onKeyDown = (event: KeyboardEvent) => {
    const at = TABS.indexOf(tab)
    const next =
      event.key === 'ArrowRight'
        ? TABS[(at + 1) % TABS.length]
        : event.key === 'ArrowLeft'
          ? TABS[(at + TABS.length - 1) % TABS.length]
          : event.key === 'Home'
            ? TABS[0]
            : event.key === 'End'
              ? TABS[TABS.length - 1]
              : undefined
    if (!next) return
    event.preventDefault()
    onTab(next)
    buttons.current[next]?.focus()
  }

  return (
    <Card className="min-w-0 flex-[2_1_340px] p-0" role="region" aria-label={`${account.name} settings`}>
      <div className="flex flex-wrap items-center gap-2 px-5 pt-4">
        <h2 className="m-0 text-base font-semibold">{account.name}</h2>
        {account.outcome ? <OutcomePill outcome={account.outcome} /> : <StagePill type={account.account_type} />}
        <span className="grow" />
        {account.outcome === null ? (
          <Button className="h-[30px]" onClick={onArchive}>
            Archive…
          </Button>
        ) : null}
      </div>
      <div role="tablist" aria-label="Account sections" className="mt-2 flex flex-wrap border-b border-line px-2.5">
        {TABS.map((t) => (
          <button
            key={t}
            ref={(node) => {
              buttons.current[t] = node
            }}
            id={`${id}-tab-${t}`}
            role="tab"
            type="button"
            aria-selected={tab === t}
            aria-controls={`${id}-panel-${t}`}
            tabIndex={tab === t ? 0 : -1}
            onClick={() => onTab(t)}
            onKeyDown={onKeyDown}
            className={cx(
              'h-[38px] cursor-pointer border-0 border-b-2 bg-transparent px-3 font-[inherit]',
              tab === t ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink',
            )}
          >
            {LABELS[t]}
          </button>
        ))}
      </div>
      <div id={`${id}-panel-${tab}`} role="tabpanel" aria-labelledby={`${id}-tab-${tab}`} className="px-5 py-4">
        {tab === 'details' ? <DetailsTab overview={overview} onSetUpFunded={onSetUpFunded} /> : null}
        {tab === 'fees' ? <FeesTab accountId={account.id} /> : null}
        {tab === 'rules' ? <RulesTab key={account.id} overview={overview} /> : null}
        {tab === 'firm-rules' ? <FirmRulesTab key={account.id} account={account} suggest={suggest} /> : null}
        {tab === 'payouts' ? <PayoutsTab key={account.id} account={account} /> : null}
        {tab === 'ledger' ? <LedgerTab key={account.id} account={account} /> : null}
      </div>
    </Card>
  )
}
