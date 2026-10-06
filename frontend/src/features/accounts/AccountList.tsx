import { useState } from 'react'
import { Button, Card, Pnl } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import { OUTCOME_LABELS, type AccountOverview, type Outcome } from '../../lib/data/model'
import { useRestoreAccount } from '../../lib/data/queries'
import { formatAmount, formatDay } from '../../lib/format'

export function StagePill({ type }: { type: 'evaluation' | 'funded' }) {
  return (
    <span
      className={cx(
        'inline-flex h-[22px] items-center rounded-md px-2 text-xs font-medium',
        type === 'funded' ? 'bg-long-bg text-pos' : 'bg-chip-selected-bg text-accent',
      )}
    >
      {type === 'funded' ? 'Funded' : 'Evaluation'}
    </span>
  )
}

const OUTCOME_STYLE: Record<Outcome, string> = {
  passed: 'bg-long-bg text-pos',
  failed: 'bg-short-bg text-neg',
  closed: 'bg-raised text-muted',
}

export function OutcomePill({ outcome }: { outcome: Outcome }) {
  return (
    <span
      className={cx('inline-flex h-[22px] items-center rounded-md px-2 text-xs font-medium', OUTCOME_STYLE[outcome])}
    >
      {OUTCOME_LABELS[outcome]}
    </span>
  )
}

/** A thin bar, with its number beside it unless `bare`. A progress bar to assistive technology, with its own label. */
export function Meter({
  value,
  label,
  tone = 'accent',
  bare = false,
}: {
  value: number
  label: string
  tone?: 'accent' | 'pos'
  bare?: boolean
}) {
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div className={cx('flex items-center gap-2', bare && 'mt-1.5')}>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className={cx('h-1.5 overflow-hidden rounded-[3px] bg-line', bare ? 'w-full' : 'w-[120px]')}
      >
        <div className={cx('h-full', tone === 'pos' ? 'bg-pos' : 'bg-accent')} style={{ width: `${percent}%` }} />
      </div>
      {bare ? null : <span className="num text-xs">{percent}%</span>}
    </div>
  )
}

const th = 'border-b border-line px-3.5 py-2.5 text-xs font-medium whitespace-nowrap text-muted'
const td = 'border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap'

function money(value: string | null): string {
  return value === null ? '—' : formatAmount(value).replace(/\.00$/, '')
}

function RestoreButton({ id, name }: { id: number; name: string }) {
  const restore = useRestoreAccount(id)
  return (
    <Button
      className="h-[30px]"
      aria-label={`Restore ${name}`}
      disabled={restore.isPending}
      onClick={() => restore.mutate(undefined)}
    >
      Restore
    </Button>
  )
}

/** Active accounts as a table you pick from, and the archived ones tucked under it. */
export function AccountList({
  overviews,
  selected,
  onSelect,
}: {
  overviews: AccountOverview[]
  selected: number | null
  onSelect: (id: number) => void
}) {
  const [showArchived, setShowArchived] = useState(false)
  const active = overviews.filter((o) => o.account.outcome === null)
  const archived = overviews.filter((o) => o.account.outcome !== null)
  return (
    <Card className="p-0" role="region" aria-label="Accounts">
      {active.length === 0 ? (
        <p className="m-0 p-5 text-muted">No active accounts. Add one to start tracking its rules and costs.</p>
      ) : (
        <div className="scroll-x">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th scope="col" className={`${th} text-left`}>
                  Account
                </th>
                <th scope="col" className={`${th} text-left`}>
                  Stage
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Size
                </th>
                <th scope="col" className={`${th} text-left`}>
                  Target progress
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Net P&amp;L
                </th>
              </tr>
            </thead>
            <tbody>
              {active.map(({ account, progress }) => (
                <tr key={account.id} className={cx(selected === account.id && 'bg-chip-selected-bg')}>
                  <td className={td}>
                    <button
                      type="button"
                      aria-pressed={selected === account.id}
                      onClick={() => onSelect(account.id)}
                      className="-my-2 cursor-pointer border-0 bg-transparent px-0 py-2 text-left font-[inherit] text-ink hover:text-accent"
                    >
                      {account.name}
                    </button>
                    <div className="text-xs text-muted">{account.firm}</div>
                  </td>
                  <td className={td}>
                    <StagePill type={account.account_type} />
                  </td>
                  <td className={`${td} num text-right`}>{money(account.account_size)}</td>
                  <td className={td}>
                    {progress.target_progress === null ? (
                      <span className="text-muted">—</span>
                    ) : (
                      <Meter value={progress.target_progress} label={`${account.name} profit target`} />
                    )}
                  </td>
                  <td className={`${td} text-right`}>
                    <Pnl value={progress.net_pnl} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
        <span className="text-[13px] text-muted">Archived ({archived.length})</span>
        <span className="grow" />
        {archived.length > 0 ? (
          <Button className="h-[30px]" aria-expanded={showArchived} onClick={() => setShowArchived((v) => !v)}>
            {showArchived ? 'Hide archived' : 'Show archived'}
          </Button>
        ) : null}
      </div>
      {showArchived ? (
        <ul className="m-0 flex list-none flex-col p-0" aria-label="Archived accounts">
          {archived.map(({ account }) => (
            <li key={account.id} className="flex flex-wrap items-center gap-3 border-t border-line-soft px-4 py-2.5">
              <button
                type="button"
                aria-pressed={selected === account.id}
                onClick={() => onSelect(account.id)}
                className="-my-2 cursor-pointer border-0 bg-transparent px-0 py-2 font-[inherit] text-ink hover:text-accent"
              >
                {account.name}
              </button>
              {account.outcome ? <OutcomePill outcome={account.outcome} /> : null}
              {account.ended_on ? (
                <span className="text-xs text-muted">ended {formatDay(account.ended_on)}</span>
              ) : null}
              {account.archive_note ? <span className="text-xs text-muted">{account.archive_note}</span> : null}
              <span className="grow" />
              <RestoreButton id={account.id} name={account.name} />
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  )
}
