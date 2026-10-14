import { useState } from 'react'
import { Button, Spinner } from '../../components/ui/primitives'
import type { AccountDetail, AccountOverview, RuleSet } from '../../lib/data/model'
import { useAccountOverviews, useRuleSet } from '../../lib/data/queries'
import { AccountList } from './AccountList'
import { AccountPanel } from './AccountPanel'
import type { Tab } from './tabs'
import { AddAccountDialog, ArchiveDialog } from './AccountDialogs'
import { BusinessCard } from './BusinessCard'
import { SectionHeader } from '../settings/SettingsLayout'

type Dialog =
  | { kind: 'add'; start?: Parameters<typeof AddAccountDialog>[0]['start']; suggest?: string }
  | { kind: 'archive'; overview: AccountOverview; passed?: boolean }
  | null

const today = () => new Date().toISOString().slice(0, 10)

/** What to search the firm rules for, for the funded account that follows a passed evaluation: the evaluation's own
 * firm, program and size when its rules are verified, else its firm and size. */
export function fundedSearch(account: AccountDetail, rules: RuleSet | null | undefined): string {
  if (rules) {
    return [rules.values['firm'], rules.values['program'], rules.values['account_size'], 'funded']
      .filter((part) => typeof part === 'string' && part)
      .join(' ')
  }
  const size = account.account_size ? `${Number(account.account_size) / 1000}K` : ''
  return [account.firm, size, 'funded'].filter(Boolean).join(' ')
}

/** Prop accounts: the list, the business totals, and the picked account's details, fees, rules and ledger. `setup` is the
 * Accounts tab's "set up your funded account" for a passed evaluation: archive it as passed, then add the funded account
 * and pick its rules. */
export function AccountsPage({
  selected,
  tab,
  onSelect,
  setup = false,
  onSetupDone = () => undefined,
}: {
  selected: number | null
  tab: Tab
  onSelect: (id: number | null, tab?: Tab) => void
  setup?: boolean
  onSetupDone?: () => void
}) {
  const overviews = useAccountOverviews()
  const [dialog, setDialog] = useState<Dialog>(null)
  const [suggestion, setSuggestion] = useState<{ id: number; text: string } | null>(null)
  const data = overviews.data
  const current = data?.find((o) => o.account.id === selected)

  const settingUp =
    setup &&
    dialog === null &&
    current !== undefined &&
    current.account.account_type === 'evaluation' &&
    current.account.outcome === null
  const archiving = dialog?.kind === 'archive' ? dialog : settingUp ? { overview: current, passed: true } : null
  const evaluationRules = useRuleSet(archiving?.overview.account.id ?? 0, archiving !== null)

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
              onSetUpFunded={() => setDialog({ kind: 'archive', overview: current, passed: true })}
              suggest={suggestion?.id === current.account.id ? suggestion.text : ''}
            />
          ) : (
            <p className="m-0 min-w-0 flex-[2_1_340px] rounded-[10px] border border-dashed border-line-strong p-6 text-center text-muted">
              Choose an account to see and change its details, fees, rules, payouts and ledger.
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
            if (dialog.suggest) setSuggestion({ id: account.id, text: dialog.suggest })
            onSelect(account.id, dialog.suggest ? 'firm-rules' : 'details')
          }}
        />
      ) : null}
      {archiving ? (
        <ArchiveDialog
          account={archiving.overview.account}
          lastTradingDay={archiving.overview.progress.last_trading_day}
          initialOutcome={archiving.passed ? 'passed' : null}
          onClose={() => {
            setDialog(null)
            if (setup) onSetupDone()
          }}
          onArchived={(setUpFunded) => {
            const archived = archiving.overview.account
            if (setUpFunded) {
              setDialog({
                kind: 'add',
                start: {
                  name: `${archived.name} funded`,
                  firm: archived.firm,
                  type: 'funded',
                  size: archived.account_size?.replace(/\.00$/, '') ?? '',
                  balance: archived.starting_balance.replace(/\.00$/, ''),
                  purchaseDate: today(),
                },
                suggest: fundedSearch(archived, evaluationRules.data),
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
