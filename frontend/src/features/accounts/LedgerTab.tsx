import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Pnl, Spinner } from '../../components/ui/primitives'
import { LEDGER_KINDS, type AccountDetail, type LedgerEntry, type LedgerKind } from '../../lib/data/model'
import { useAddLedgerEntry, useDeleteLedgerEntry, useLedger } from '../../lib/data/queries'
import { formatDay } from '../../lib/format'
import { parseAmount, sumMoney } from '../../lib/money'

const KINDS = Object.keys(LEDGER_KINDS) as LedgerKind[]
const today = () => new Date().toISOString().slice(0, 10)

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'That did not work. Nothing was changed.'
}

/** The likeliest next entry: the purchase fee if none is recorded, else a payout for a funded account or a reset. */
export function nextKind(account: Pick<AccountDetail, 'account_type'>, entries: readonly LedgerEntry[]): LedgerKind {
  if (!entries.some((e) => e.kind !== 'payout'))
    return account.account_type === 'funded' ? 'activation_fee' : 'evaluation_fee'
  return account.account_type === 'funded' ? 'payout' : 'reset_fee'
}

function EntryRow({ accountId, entry }: { accountId: number; entry: LedgerEntry }) {
  const remove = useDeleteLedgerEntry(accountId)
  const [asking, setAsking] = useState(false)
  const payout = entry.kind === 'payout'
  return (
    <tr>
      <td className="num border-b border-line-soft px-3 py-2 whitespace-nowrap">{formatDay(entry.entry_date)}</td>
      <td className="border-b border-line-soft px-3 py-2">
        {LEDGER_KINDS[entry.kind]}
        {entry.note ? <div className="text-xs text-muted">{entry.note}</div> : null}
      </td>
      <td className="border-b border-line-soft px-3 py-2 text-right whitespace-nowrap">
        <Pnl value={payout ? entry.amount : `-${entry.amount}`} />
      </td>
      <td className="border-b border-line-soft px-3 py-2 text-right whitespace-nowrap">
        {asking ? (
          <span className="inline-flex items-center gap-1.5">
            <Button
              className="h-[28px] border-neg text-neg"
              disabled={remove.isPending}
              aria-label={`Confirm delete ${LEDGER_KINDS[entry.kind]} ${entry.entry_date}`}
              onClick={() => remove.mutate(entry.id)}
            >
              Delete
            </Button>
            <Button className="h-[28px]" onClick={() => setAsking(false)}>
              Keep
            </Button>
          </span>
        ) : (
          <Button
            variant="ghost"
            className="h-[28px]"
            aria-label={`Delete ${LEDGER_KINDS[entry.kind]} ${entry.entry_date}`}
            onClick={() => setAsking(true)}
          >
            Delete
          </Button>
        )}
      </td>
    </tr>
  )
}

/** The money put into this account and taken out of it. Kept apart from trading results. */
export function LedgerTab({ account }: { account: AccountDetail }) {
  const ledger = useLedger(account.id)
  const add = useAddLedgerEntry(account.id)
  const entries = ledger.data ?? []
  const [kind, setKind] = useState<LedgerKind | null>(null) // null: the likeliest next one
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(today())
  const [note, setNote] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const chosenKind = kind ?? nextKind(account, entries)

  const costs = sumMoney(entries.filter((e) => e.kind !== 'payout').map((e) => e.amount))
  const payouts = sumMoney(entries.filter((e) => e.kind === 'payout').map((e) => e.amount))

  const submit = (event: FormEvent) => {
    event.preventDefault()
    let parsed: string | null
    try {
      parsed = parseAmount(amount)
    } catch (error) {
      setProblem(messageOf(error))
      return
    }
    if (parsed === null) {
      setProblem('Type the amount.')
      return
    }
    setProblem(null)
    add.mutate(
      { kind: chosenKind, amount: parsed, entry_date: date, note },
      {
        onSuccess: () => {
          setAmount('')
          setNote('')
          setKind(null)
        },
      },
    )
  }

  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex flex-wrap gap-x-8 gap-y-2" aria-label="Totals for this account" role="group">
        <div>
          <div className="text-xs text-muted">Spent</div>
          <div className="num text-lg font-semibold text-neg">
            {costs === '0.00' ? '$0.00' : <Pnl value={`-${costs}`} />}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">Payouts</div>
          <div className="num text-lg font-semibold">
            <Pnl value={payouts} />
          </div>
        </div>
      </div>
      {ledger.isPending ? <Spinner label="Loading the ledger" /> : null}
      {ledger.isError ? (
        <p role="alert" className="m-0">
          The ledger could not be loaded.
        </p>
      ) : null}
      {ledger.data && entries.length === 0 ? <p className="m-0 text-muted">Nothing recorded yet.</p> : null}
      {entries.length > 0 ? (
        <div className="scroll-x">
          <table className="w-full border-collapse text-[13px]" aria-label="Ledger entries">
            <thead>
              <tr>
                <th scope="col" className="border-b border-line px-3 py-2 text-left text-xs font-medium text-muted">
                  Date
                </th>
                <th scope="col" className="border-b border-line px-3 py-2 text-left text-xs font-medium text-muted">
                  What for
                </th>
                <th scope="col" className="border-b border-line px-3 py-2 text-right text-xs font-medium text-muted">
                  Amount
                </th>
                <th scope="col" className="border-b border-line px-3 py-2 text-right text-xs font-medium text-muted">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <EntryRow key={entry.id} accountId={account.id} entry={entry} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <form onSubmit={submit} aria-label="Add an entry" className="flex flex-col gap-3 border-t border-line pt-3.5">
        <div className="flex flex-wrap gap-3">
          <div className="min-w-[150px] flex-1">
            <Field id="ledger-kind" label="What for">
              <select
                id="ledger-kind"
                value={chosenKind}
                onChange={(e) => setKind(e.target.value as LedgerKind)}
                className={CONTROL}
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {LEDGER_KINDS[k]}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="w-28">
            <Field id="ledger-amount" label="Amount ($)">
              <input
                id="ledger-amount"
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className={`${CONTROL} num`}
              />
            </Field>
          </div>
          <div className="w-40">
            <Field id="ledger-date" label="Date">
              <input
                id="ledger-date"
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className={CONTROL}
              />
            </Field>
          </div>
        </div>
        <Field id="ledger-note" label="Note (optional)">
          <input id="ledger-note" value={note} onChange={(e) => setNote(e.target.value)} className={CONTROL} />
        </Field>
        {problem || add.isError ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {problem ?? messageOf(add.error)}
          </p>
        ) : null}
        <div>
          <Button type="submit" variant="primary" disabled={add.isPending}>
            {add.isPending ? 'Adding…' : 'Add entry'}
          </Button>
        </div>
      </form>
    </div>
  )
}
