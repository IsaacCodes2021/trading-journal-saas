import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Button, Segmented } from '../../components/ui/primitives'
import {
  FIRMS,
  OUTCOMES_BY_TYPE,
  OUTCOME_LABELS,
  type AccountDetail,
  type AccountDetailsInput,
  type Outcome,
} from '../../lib/data/model'
import { useArchiveAccount, useCreateAccount } from '../../lib/data/queries'
import type { AccountType } from '../../lib/filters'
import { parseAmount } from '../../lib/money'

const TYPES = [
  { value: 'evaluation', label: 'Evaluation' },
  { value: 'funded', label: 'Funded' },
] as const

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'That did not work. Nothing was changed.'
}

/** Turns what was typed in the money boxes into what the account stores, or says which box is wrong. */
export function readAmounts(
  size: string,
  balance: string,
): { size: string | null; balance: string } | { problem: Record<string, string> } {
  const problem: Record<string, string> = {}
  let sizeValue: string | null = null
  let balanceValue = '0.00'
  try {
    sizeValue = parseAmount(size)
  } catch (error) {
    problem['size'] = messageOf(error)
  }
  try {
    balanceValue = parseAmount(balance) ?? '0.00'
  } catch (error) {
    problem['balance'] = messageOf(error)
  }
  return Object.keys(problem).length > 0 ? { problem } : { size: sizeValue, balance: balanceValue }
}

/** The name, firm, type and money of an account, as one form. Used to add an account (and shaped to edit one). */
export function AccountForm({
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial: { name: string; firm: string; type: AccountType; size: string; balance: string }
  submitLabel: string
  pending: boolean
  error: string | null
  onSubmit: (input: AccountDetailsInput) => void
  onCancel?: () => void
}) {
  const [name, setName] = useState(initial.name)
  const [firm, setFirm] = useState(initial.firm)
  const [type, setType] = useState<AccountType>(initial.type)
  const [size, setSize] = useState(initial.size)
  const [balance, setBalance] = useState(initial.balance)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const amounts = readAmounts(size, balance)
    if ('problem' in amounts) {
      setFieldErrors(amounts.problem)
      return
    }
    setFieldErrors({})
    onSubmit({ name, firm, account_type: type, account_size: amounts.size, starting_balance: amounts.balance })
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3.5">
      <Field id="account-name" label="Account name" hint="Whatever you call it, like Apex 50K #1.">
        <input id="account-name" value={name} onChange={(e) => setName(e.target.value)} className={CONTROL} />
      </Field>
      <div className="flex flex-wrap gap-3">
        <div className="min-w-[180px] flex-1">
          <Field id="account-firm" label="Firm">
            <select id="account-firm" value={firm} onChange={(e) => setFirm(e.target.value)} className={CONTROL}>
              {FIRMS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium">Stage</span>
          <Segmented label="Stage" options={TYPES} value={type} onChange={setType} />
        </div>
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="min-w-[160px] flex-1">
          <Field id="account-size" label="Account size" hint="Optional, like 50,000." error={fieldErrors['size']}>
            <input
              id="account-size"
              inputMode="decimal"
              value={size}
              onChange={(e) => setSize(e.target.value)}
              className={`${CONTROL} num`}
            />
          </Field>
        </div>
        <div className="min-w-[160px] flex-1">
          <Field
            id="account-balance"
            label="Starting balance"
            hint="Usually the same as the size."
            error={fieldErrors['balance']}
          >
            <input
              id="account-balance"
              inputMode="decimal"
              value={balance}
              onChange={(e) => setBalance(e.target.value)}
              className={`${CONTROL} num`}
            />
          </Field>
        </div>
      </div>
      {error ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        {onCancel ? (
          <Button onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
        ) : null}
        <Button type="submit" variant="primary" disabled={pending}>
          {pending ? 'Saving…' : submitLabel}
        </Button>
      </div>
    </form>
  )
}

/** The dialog for a new account. `start` pre-fills it (the funded account that follows a passed evaluation). */
export function AddAccountDialog({
  start,
  onClose,
  onCreated,
}: {
  start?: Partial<{ name: string; firm: string; type: AccountType; size: string; balance: string }>
  onClose: () => void
  onCreated: (account: AccountDetail) => void
}) {
  const create = useCreateAccount()
  return (
    <Modal label="Add account" onClose={create.isPending ? () => undefined : onClose} className="max-w-[520px]">
      <h2 className="m-0 text-base font-semibold">Add account</h2>
      <AccountForm
        initial={{ name: '', firm: 'Apex', type: 'evaluation', size: '', balance: '', ...start }}
        submitLabel="Add account"
        pending={create.isPending}
        error={create.isError ? messageOf(create.error) : null}
        onSubmit={(input) => create.mutate(input, { onSuccess: onCreated })}
        onCancel={onClose}
      />
    </Modal>
  )
}

const today = () => new Date().toISOString().slice(0, 10)

/** Ask how an account ended, then archive it. A passed evaluation can go straight on to setting up its funded account. */
export function ArchiveDialog({
  account,
  lastTradingDay,
  onClose,
  onArchived,
}: {
  account: AccountDetail
  lastTradingDay: string | null
  onClose: () => void
  onArchived: (setUpFunded: boolean) => void
}) {
  const archive = useArchiveAccount(account.id)
  const choices = OUTCOMES_BY_TYPE[account.account_type]
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [endedOn, setEndedOn] = useState(lastTradingDay ?? today())
  const [note, setNote] = useState('')
  const [funded, setFunded] = useState(true)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (outcome === null) return
    archive.mutate(
      { outcome, ended_on: endedOn, note },
      { onSuccess: () => onArchived(outcome === 'passed' && funded) },
    )
  }

  return (
    <Modal
      label={`Archive ${account.name}`}
      onClose={archive.isPending ? () => undefined : onClose}
      className="max-w-[480px]"
    >
      <form onSubmit={submit} className="flex flex-col gap-3.5">
        <h2 className="m-0 text-base font-semibold">Archive {account.name}</h2>
        <p className="m-0 text-muted">
          Its trades stay in your journal. They are hidden from the dashboard and trade log unless you ask for archived
          accounts, and you can restore the account at any time.
        </p>
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium" id="how-ended">
            How did it end?
          </span>
          <div role="group" aria-labelledby="how-ended" className="flex gap-2">
            {choices.map((o) => (
              <Button
                key={o}
                aria-pressed={outcome === o}
                variant={outcome === o ? 'primary' : 'default'}
                onClick={() => setOutcome(o)}
              >
                {OUTCOME_LABELS[o]}
              </Button>
            ))}
          </div>
        </div>
        <Field
          id="ended-on"
          label="Ended on"
          hint={lastTradingDay ? "Starts at the account's last trading day." : undefined}
        >
          <input
            id="ended-on"
            type="date"
            value={endedOn}
            onChange={(e) => setEndedOn(e.target.value)}
            className={CONTROL}
          />
        </Field>
        <Field id="archive-note" label="Note (optional)">
          <textarea
            id="archive-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Hit the trailing drawdown on CPI day"
            rows={2}
            className="w-full rounded-lg border border-line-strong bg-bg p-3 font-[inherit] text-ink"
          />
        </Field>
        {outcome === 'passed' ? (
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={funded} onChange={(e) => setFunded(e.target.checked)} />
            Set up the funded account next
          </label>
        ) : null}
        {archive.isError ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {messageOf(archive.error)}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button onClick={onClose} disabled={archive.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={outcome === null || archive.isPending}>
            {archive.isPending ? 'Archiving…' : 'Archive account'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
