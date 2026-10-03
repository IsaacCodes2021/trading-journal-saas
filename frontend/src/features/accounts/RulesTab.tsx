import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button } from '../../components/ui/primitives'
import {
  TRAILING_LABELS,
  TRAILING_TYPES,
  type AccountOverview,
  type AccountRulesInput,
  type TrailingType,
} from '../../lib/data/model'
import { useUpdateRules } from '../../lib/data/queries'
import { formatAmount, formatPnl } from '../../lib/format'
import { parseAmount, toChartNumber } from '../../lib/money'
import { Meter } from './AccountList'

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'That did not work. Nothing was changed.'
}

/** A saved amount as the box shows it: "3000.00" to "3000", nothing to "". */
const shown = (value: string | null) => (value === null ? '' : value.replace(/\.00$/, ''))

function Progress({
  label,
  barLabel,
  text,
  value,
  tone,
}: {
  label: string
  barLabel: string
  text: string
  value: number
  tone?: 'accent' | 'pos'
}) {
  return (
    <div>
      <div className="flex justify-between text-[13px]">
        <span>{label}</span>
        <span className="num">{text}</span>
      </div>
      <Meter value={value} label={barLabel} bare {...(tone ? { tone } : {})} />
    </div>
  )
}

/** The firm's rules for the account, and how the closed trades stand against them. Informational, never the firm's own figure. */
export function RulesTab({ overview }: { overview: AccountOverview }) {
  const { account, progress } = overview
  const save = useUpdateRules(account.id)
  const [target, setTarget] = useState(shown(account.profit_target))
  const [drawdown, setDrawdown] = useState(shown(account.max_drawdown))
  const [trailing, setTrailing] = useState<TrailingType | ''>(account.trailing_type ?? '')
  const [lock, setLock] = useState(account.trail_lock_enabled)
  const [lockLevel, setLockLevel] = useState(shown(account.trail_lock_level))
  const [daily, setDaily] = useState(shown(account.daily_loss_limit))
  const [problems, setProblems] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState(false)

  const edit =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value)
      setSaved(false)
    }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const found: Record<string, string> = {}
    const read = (key: string, text: string): string | null => {
      try {
        return parseAmount(text)
      } catch (error) {
        found[key] = messageOf(error)
        return null
      }
    }
    const input: AccountRulesInput = {
      profit_target: read('target', target),
      max_drawdown: read('drawdown', drawdown),
      trailing_type: trailing === '' ? null : trailing,
      trail_lock_enabled: lock,
      trail_lock_level: lock ? read('lock', lockLevel) : null,
      daily_loss_limit: read('daily', daily),
    }
    setProblems(found)
    if (Object.keys(found).length > 0) return
    save.mutate(input, { onSuccess: () => setSaved(true) })
  }

  const net = progress.net_pnl
  return (
    <form onSubmit={submit} className="flex flex-col gap-3.5">
      <div className="flex flex-wrap gap-3">
        <div className="min-w-[130px] flex-1">
          <Field id="rule-target" label="Profit target" error={problems['target']}>
            <input
              id="rule-target"
              inputMode="decimal"
              value={target}
              onChange={(e) => edit(setTarget)(e.target.value)}
              className={`${CONTROL} num`}
            />
          </Field>
        </div>
        <div className="min-w-[130px] flex-1">
          <Field id="rule-drawdown" label="Max drawdown" error={problems['drawdown']}>
            <input
              id="rule-drawdown"
              inputMode="decimal"
              value={drawdown}
              onChange={(e) => edit(setDrawdown)(e.target.value)}
              className={`${CONTROL} num`}
            />
          </Field>
        </div>
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="min-w-[130px] flex-1">
          <Field id="rule-trailing" label="Trailing type">
            <select
              id="rule-trailing"
              value={trailing}
              onChange={(e) => edit(setTrailing)(e.target.value as TrailingType | '')}
              className={CONTROL}
            >
              <option value="">None</option>
              {TRAILING_TYPES.map((t) => (
                <option key={t} value={t}>
                  {TRAILING_LABELS[t]}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="min-w-[130px] flex-1">
          <Field id="rule-daily" label="Daily loss limit" hint="Leave empty for none." error={problems['daily']}>
            <input
              id="rule-daily"
              inputMode="decimal"
              value={daily}
              onChange={(e) => edit(setDaily)(e.target.value)}
              className={`${CONTROL} num`}
            />
          </Field>
        </div>
      </div>
      {trailing !== '' ? (
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex h-10 items-center gap-2 text-[13px]">
            <input type="checkbox" checked={lock} onChange={(e) => edit(setLock)(e.target.checked)} />
            The trailing stops at a level
          </label>
          {lock ? (
            <div className="min-w-[130px] flex-1">
              <Field id="rule-lock" label="Stops trailing at" error={problems['lock']}>
                <input
                  id="rule-lock"
                  inputMode="decimal"
                  value={lockLevel}
                  onChange={(e) => edit(setLockLevel)(e.target.value)}
                  className={`${CONTROL} num`}
                />
              </Field>
            </div>
          ) : null}
        </div>
      ) : null}

      {account.profit_target !== null || account.max_drawdown !== null ? (
        <div className="flex flex-col gap-3 border-t border-line pt-3.5">
          {account.profit_target !== null && progress.target_progress !== null ? (
            <Progress
              label="Profit target"
              barLabel="Progress to the profit target"
              text={`${formatPnl(net)} / ${formatAmount(account.profit_target).replace(/\.00$/, '')}`}
              value={progress.target_progress}
            />
          ) : null}
          {account.max_drawdown !== null && progress.drawdown_left !== null ? (
            <Progress
              label="Drawdown buffer left"
              barLabel="Drawdown buffer left"
              text={`${formatAmount(progress.drawdown_left).replace(/\.00$/, '')} / ${formatAmount(account.max_drawdown).replace(/\.00$/, '')}`}
              value={toChartNumber(progress.drawdown_left) / toChartNumber(account.max_drawdown)}
              tone="pos"
            />
          ) : null}
        </div>
      ) : null}
      <p className="m-0 text-xs text-muted">
        Informational only. End-of-day drawdown is exact from closed trades; intraday trailing is approximate because
        the CSV has no open-trade peaks. May differ from the firm&rsquo;s official figure.
      </p>
      {save.isError ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {messageOf(save.error)}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save rules'}
        </Button>
        {saved ? (
          <span role="status" className="text-[13px] text-pos">
            Saved
          </span>
        ) : null}
      </div>
    </form>
  )
}
