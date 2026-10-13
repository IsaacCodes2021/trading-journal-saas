import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { Modal } from '../../components/ui/Modal'
import { Popover } from '../../components/ui/Popover'
import { cx } from '../../components/ui/cx'
import { Button, Card, Pnl, Spinner } from '../../components/ui/primitives'
import {
  PHASE_LABELS,
  type AccountRow,
  type AccountStatus,
  type AccountsTotals,
  type RuleStatus,
  type Spectrum,
} from '../../lib/data/model'
import { useAccountsOverview, useDismissPassPrompt } from '../../lib/data/queries'
import { formatMoney } from '../../lib/money'
import { RULE_STATUS_LABELS, STATUS_LABELS } from '../../lib/rules'

/*
 * The Accounts tab: every active account against its verified rules, read-only. The numbers are the server's engine
 * (`compliance.py`) working on closed trades only, so a rule that needs open equity says "Not verifiable from imports"
 * rather than pass. Nothing is changed here: each account's ⋯ menu leads to its settings.
 */

const STATUS_TONES: Record<AccountStatus, string> = {
  ok: 'border-line text-muted',
  at_risk: 'border-warn-line text-warn',
  breached: 'border-neg text-neg',
  passed: 'border-pos text-pos',
  payout_ready: 'border-gold-line bg-gold-bg text-gold',
}

const RULE_TONES: Record<RuleStatus, string> = {
  pass: 'text-pos',
  fail: 'text-neg',
  'n/a': 'text-muted',
  not_verifiable: 'text-warn',
}

const BUTTON_LINK =
  'inline-flex h-9 items-center justify-center rounded-lg border border-line bg-raised px-3 text-ink no-underline hover:border-line-strong'

function StatusBadge({ status }: { status: AccountStatus }) {
  return (
    <span
      className={cx(
        'inline-flex h-[22px] items-center rounded-full border px-2 text-xs font-medium whitespace-nowrap',
        STATUS_TONES[status],
      )}
    >
      {STATUS_LABELS[status]}
    </span>
  )
}

function NoRules({ row }: { row: AccountRow }) {
  return (
    <Link
      to="/settings/accounts"
      search={{ account: row.account.id, tab: 'firm-rules' }}
      className="text-[13px] whitespace-nowrap"
    >
      No rules set
    </Link>
  )
}

/** What the bar's ends mean, in words: the tooltip and what a screen reader hears. */
export function spectrumText(s: Spectrum): string {
  const parts: string[] = []
  if (s.to_breach !== null) {
    parts.push(
      `${formatMoney(s.to_breach)} above the ${s.breach_kind === 'daily_loss' ? "day's loss limit" : 'drawdown floor'}`,
    )
  }
  if (s.to_target !== null) {
    const what = s.target_kind === 'payout' ? 'the payout balance' : 'the profit target'
    parts.push(s.to_target === '0.00' ? `at ${what}` : `${formatMoney(s.to_target)} to ${what}`)
  }
  return parts.join(' · ')
}

/** Breach on the left (red), goal on the right (gold to green), a marker where the balance is. */
function SpectrumBar({ spectrum }: { spectrum: Spectrum | null }) {
  if (spectrum === null || spectrum.position === null) return <span className="text-muted">—</span>
  const label = spectrumText(spectrum)
  return (
    <div
      role="img"
      aria-label={`Breach to goal: ${label}`}
      title={label}
      className="relative h-2.5 w-32 rounded-full"
      style={{ background: 'linear-gradient(to right, var(--neg), var(--gold), var(--pos))' }}
    >
      <span
        aria-hidden="true"
        className="absolute top-1/2 h-4 w-1 -translate-x-1/2 -translate-y-1/2 rounded bg-ink ring-2 ring-panel"
        style={{ left: `${Math.round(spectrum.position * 100)}%` }}
      />
    </div>
  )
}

function PayoutText({ row }: { row: AccountRow }) {
  if (!row.rules_set) return <span className="text-muted">—</span>
  if (row.payout?.ready && row.payout.gross) {
    return (
      <span className="font-medium text-gold">
        Payout ready: <span className="num">{formatMoney(row.payout.gross)}</span> gross
        {row.payout.net ? (
          <>
            {' / '}
            <span className="num">{formatMoney(row.payout.net)}</span> net
          </>
        ) : null}
      </span>
    )
  }
  return <span className="text-muted">{row.progress || '—'}</span>
}

function Inactivity({ row }: { row: AccountRow }) {
  const inactivity = row.inactivity
  if (!inactivity) return null
  return (
    <span className={cx('text-[13px]', inactivity.days_left !== null && inactivity.days_left <= 7 && 'text-warn')}>
      {inactivity.message}
    </span>
  )
}

function AccountMenu({ row }: { row: AccountRow }) {
  return (
    <Popover
      label={`${row.account.name} menu`}
      button={<span aria-label={`More for ${row.account.name}`}>⋯</span>}
      buttonClassName="h-7 px-2"
    >
      {(close) => (
        <Link
          to="/settings/accounts"
          search={{ account: row.account.id, tab: 'details' }}
          onClick={close}
          className="block rounded-md px-2 py-1.5 text-ink no-underline hover:bg-raised"
        >
          Account settings
        </Link>
      )}
    </Popover>
  )
}

/** Every rule of one account, with its current value, its limit, and whether it holds. Read-only. */
function RulesModal({ row, onClose }: { row: AccountRow; onClose: () => void }) {
  return (
    <Modal label={`${row.account.name} rules`} onClose={onClose} className="max-w-[860px]">
      <div className="flex items-center gap-2">
        <h2 className="m-0 text-base font-semibold">{row.account.name}: rules</h2>
        {row.status ? <StatusBadge status={row.status} /> : null}
        <span className="grow" />
        <Button onClick={onClose} data-autofocus>
          Close
        </Button>
      </div>
      <p className="m-0 text-[13px] text-muted">
        Worked out from your imported (closed) trades. A rule that depends on open profit or loss during a trade is
        marked &ldquo;Not verifiable from imports&rdquo;; a breach the closed trades prove is still shown.
      </p>
      <div className="scroll-x">
        <table className="w-full border-collapse text-[13px]" aria-label="Rules">
          <thead>
            <tr>
              {['Rule', 'Result', 'Now', 'Limit', 'Why'].map((h) => (
                <th
                  key={h}
                  scope="col"
                  className="border-b border-line px-3 py-2 text-left text-xs font-medium text-muted"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {row.rules.map((rule) => (
              <tr key={rule.key}>
                <th scope="row" className="border-b border-line-soft px-3 py-2 text-left align-top font-normal">
                  {rule.label}
                  <div className="text-xs text-muted">{rule.group}</div>
                </th>
                <td
                  className={cx(
                    'border-b border-line-soft px-3 py-2 align-top font-medium whitespace-nowrap',
                    RULE_TONES[rule.status],
                  )}
                >
                  {RULE_STATUS_LABELS[rule.status]}
                </td>
                <td className="num border-b border-line-soft px-3 py-2 align-top whitespace-nowrap">
                  {rule.current ?? '—'}
                </td>
                <td className="num border-b border-line-soft px-3 py-2 align-top whitespace-nowrap">
                  {rule.limit ?? '—'}
                </td>
                <td className="border-b border-line-soft px-3 py-2 align-top text-muted">{rule.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  )
}

function RulesButton({ row, onOpen }: { row: AccountRow; onOpen: () => void }) {
  if (!row.rules_set) return <NoRules row={row} />
  return (
    <Button className="h-[30px]" onClick={onOpen} aria-label={`Rules of ${row.account.name}`}>
      Rules
    </Button>
  )
}

function PassPrompt({ row }: { row: AccountRow }) {
  const dismiss = useDismissPassPrompt()
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-3 rounded-[10px] border border-pos bg-long-bg px-4 py-3 text-[13px]"
    >
      <span>
        <strong>Evaluation passed: set up your funded account.</strong> {row.account.name} reached its target with every
        rule met.
      </span>
      <span className="grow" />
      <Link
        to="/settings/accounts"
        search={{ account: row.account.id, tab: 'details', setup: 'funded' }}
        className={cx(BUTTON_LINK, 'border-accent bg-accent font-semibold text-accent-on hover:bg-accent-hover')}
      >
        Set up
      </Link>
      <Button disabled={dismiss.isPending} onClick={() => dismiss.mutate(row.account.id)}>
        Dismiss
      </Button>
    </div>
  )
}

const TH = 'border-b border-line px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted'
const TD = 'border-b border-line-soft px-3 py-2.5 align-middle'

function AccountsTable({
  rows,
  totals,
  onRules,
}: {
  rows: AccountRow[]
  totals: AccountsTotals
  onRules: (row: AccountRow) => void
}) {
  return (
    <Card className="hidden overflow-hidden md:block">
      <div className="scroll-x">
        <table className="w-full border-collapse text-[13px]" aria-label="Active accounts">
          <thead>
            <tr>
              <th scope="col" className={TH}>
                Account
              </th>
              <th scope="col" className={TH}>
                Firm / program
              </th>
              <th scope="col" className={TH}>
                Phase
              </th>
              <th scope="col" className={cx(TH, 'text-right')}>
                Balance
              </th>
              <th scope="col" className={cx(TH, 'text-right')}>
                Net P&amp;L
              </th>
              <th scope="col" className={TH}>
                Status
              </th>
              <th scope="col" className={TH}>
                Breach ↔ goal
              </th>
              <th scope="col" className={TH}>
                Payout
              </th>
              <th scope="col" className={cx(TH, 'text-right')}>
                Consistency room
              </th>
              <th scope="col" className={TH}>
                Inactivity
              </th>
              <th scope="col" className={TH}>
                <span className="sr-only">Rules</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const ready = row.payout?.ready === true
              return (
                <tr key={row.account.id} className={cx(ready && 'bg-gold-bg')} data-payout-ready={ready || undefined}>
                  <th
                    scope="row"
                    className={cx(
                      TD,
                      'text-left font-medium',
                      ready ? 'border-l-4 border-l-gold-line' : 'border-l-4 border-l-transparent',
                    )}
                  >
                    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                      {row.account.name}
                      <AccountMenu row={row} />
                    </span>
                  </th>
                  <td className={TD}>
                    {row.account.firm}
                    {row.program ? <div className="text-xs text-muted">{row.program}</div> : null}
                  </td>
                  <td className={cx(TD, 'whitespace-nowrap')}>{PHASE_LABELS[row.phase]}</td>
                  <td className={cx(TD, 'num text-right whitespace-nowrap')}>{formatMoney(row.balance)}</td>
                  <td className={cx(TD, 'text-right whitespace-nowrap')}>
                    <Pnl value={row.net_pnl} />
                  </td>
                  <td className={TD}>{row.status ? <StatusBadge status={row.status} /> : <NoRules row={row} />}</td>
                  <td className={TD}>
                    <SpectrumBar spectrum={row.spectrum} />
                  </td>
                  <td className={cx(TD, 'min-w-[180px]')}>
                    <PayoutText row={row} />
                  </td>
                  <td className={cx(TD, 'num text-right whitespace-nowrap')}>
                    {row.consistency_headroom ? formatMoney(row.consistency_headroom) : ''}
                  </td>
                  <td className={TD}>
                    <Inactivity row={row} />
                  </td>
                  <td className={cx(TD, 'text-right')}>
                    <RulesButton row={row} onOpen={() => onRules(row)} />
                  </td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" colSpan={3} className="px-3 py-2.5 text-left text-xs font-medium text-muted">
                Totals
              </th>
              <td className="num px-3 py-2.5 text-right font-semibold whitespace-nowrap">
                {formatMoney(totals.balance)}
              </td>
              <td className="px-3 py-2.5 text-right font-semibold whitespace-nowrap">
                <Pnl value={totals.net_pnl} />
              </td>
              <td colSpan={2} />
              <td colSpan={4} className="px-3 py-2.5 text-[13px]">
                <TotalsText totals={totals} />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  )
}

function TotalsText({ totals }: { totals: AccountsTotals }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span>
        Withdrawable now: <span className="num font-semibold text-gold">{formatMoney(totals.withdrawable_gross)}</span>{' '}
        gross / <span className="num font-semibold">{formatMoney(totals.withdrawable_net)}</span> net
      </span>
      <span className="text-muted">
        Withdrawn so far: <span className="num">{formatMoney(totals.withdrawn_gross)}</span> gross /{' '}
        <span className="num">{formatMoney(totals.withdrawn_net)}</span> net
      </span>
    </span>
  )
}

/** The same rows as cards, for a phone. */
function AccountCards({
  rows,
  totals,
  onRules,
}: {
  rows: AccountRow[]
  totals: AccountsTotals
  onRules: (row: AccountRow) => void
}) {
  return (
    <ul className="m-0 flex list-none flex-col gap-3 p-0 md:hidden" aria-label="Active accounts">
      {rows.map((row) => {
        const ready = row.payout?.ready === true
        return (
          <li key={row.account.id}>
            <Card className={cx('flex flex-col gap-2.5 p-4', ready && 'border-l-4 border-l-gold-line bg-gold-bg')}>
              <div className="flex items-center gap-2">
                <span className="font-semibold">{row.account.name}</span>
                <AccountMenu row={row} />
                <span className="grow" />
                {row.status ? <StatusBadge status={row.status} /> : <NoRules row={row} />}
              </div>
              <div className="text-[13px] text-muted">
                {row.account.firm}
                {row.program ? ` · ${row.program}` : ''} · {PHASE_LABELS[row.phase]}
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-[13px]">
                <span>
                  Balance <span className="num font-semibold">{formatMoney(row.balance)}</span>
                </span>
                <span>
                  Net <Pnl value={row.net_pnl} className="font-semibold" />
                </span>
                {row.consistency_headroom ? (
                  <span>
                    Consistency room <span className="num">{formatMoney(row.consistency_headroom)}</span>
                  </span>
                ) : null}
              </div>
              <SpectrumBar spectrum={row.spectrum} />
              <div className="text-[13px]">
                <PayoutText row={row} />
              </div>
              <Inactivity row={row} />
              {row.rules_set ? (
                <div>
                  <RulesButton row={row} onOpen={() => onRules(row)} />
                </div>
              ) : null}
            </Card>
          </li>
        )
      })}
      <li>
        <Card className="flex flex-col gap-1 p-4 text-[13px]">
          <span className="font-semibold">Totals</span>
          <span>
            Balance <span className="num font-semibold">{formatMoney(totals.balance)}</span> · Net{' '}
            <Pnl value={totals.net_pnl} />
          </span>
          <TotalsText totals={totals} />
        </Card>
      </li>
    </ul>
  )
}

export function AccountsOverviewPage() {
  const overview = useAccountsOverview()
  const [rulesOf, setRulesOf] = useState<AccountRow | null>(null)
  const data = overview.data

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h1 className="m-0 text-xl font-semibold">Accounts</h1>
        <p className="m-0 text-[13px] text-muted">
          Each active account against its verified rules, from your imported trades. To change an account, its rules or
          its payouts, open its settings from the ⋯ menu.
        </p>
      </div>
      {overview.isPending ? <Spinner label="Loading your accounts" /> : null}
      {overview.isError ? (
        <p role="alert" className="m-0">
          Your accounts could not be loaded.
        </p>
      ) : null}
      {data ? (
        <>
          {data.accounts
            .filter((row) => row.pass_prompt)
            .map((row) => (
              <PassPrompt key={row.account.id} row={row} />
            ))}
          {data.accounts.length === 0 ? (
            <Card className="p-6 text-center text-muted">
              No active accounts.{' '}
              <Link to="/settings/accounts" search={{}}>
                Add one in settings
              </Link>
              .
            </Card>
          ) : (
            <>
              <AccountsTable rows={data.accounts} totals={data.totals} onRules={setRulesOf} />
              <AccountCards rows={data.accounts} totals={data.totals} onRules={setRulesOf} />
            </>
          )}
        </>
      ) : null}
      {rulesOf ? <RulesModal row={rulesOf} onClose={() => setRulesOf(null)} /> : null}
    </div>
  )
}
