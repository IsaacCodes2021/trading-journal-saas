import Decimal from 'decimal.js'
import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Spinner } from '../../components/ui/primitives'
import type { AccountDetail, LedgerEntry } from '../../lib/data/model'
import { useAddPayout, usePayouts } from '../../lib/data/queries'
import { formatDay } from '../../lib/format'
import { formatMoney, parseAmount } from '../../lib/money'
import { netOf } from '../../lib/rules'

const today = () => new Date().toISOString().slice(0, 10)

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'That did not work. Nothing was changed.'
}

const CELL = 'border-b border-line-soft px-3 py-2 whitespace-nowrap'
const HEAD = 'border-b border-line px-3 py-2 text-xs font-medium text-muted'

/** A percentage as it is written: "90", "87.5", "100". */
const plain = (pct: string) => new Decimal(pct).toString()

function splitText(pct: string | null): string {
  return pct === null ? '—' : `${plain(pct)}%`
}

function PayoutRow({ entry }: { entry: LedgerEntry }) {
  return (
    <tr>
      <td className={`${CELL} num`}>{entry.payout_number ?? '—'}</td>
      <td className={`${CELL} num`}>{formatDay(entry.entry_date)}</td>
      <td className={`${CELL} num text-right`}>{entry.gross_amount ? formatMoney(entry.gross_amount) : '—'}</td>
      <td className={`${CELL} num text-right`}>{splitText(entry.profit_split_pct)}</td>
      <td className={`${CELL} num text-right font-medium`}>{formatMoney(entry.amount)}</td>
      <td className={`${CELL} num text-right`}>
        {entry.payout_fee && entry.payout_fee !== '0.00' ? formatMoney(entry.payout_fee) : '—'}
      </td>
      <td className="border-b border-line-soft px-3 py-2 text-muted">{entry.note}</td>
    </tr>
  )
}

/** The account's payouts: numbered by date, each with its gross, split, net and fee, and a form to log the next one. */
export function PayoutsTab({ account }: { account: AccountDetail }) {
  const payouts = usePayouts(account.id)
  const add = useAddPayout(account.id)
  const [date, setDate] = useState(today())
  const [gross, setGross] = useState('')
  const [split, setSplit] = useState<string | null>(null) // null: the split the rules promise
  const [fee, setFee] = useState('')
  const [note, setNote] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const data = payouts.data
  const shownSplit = split ?? (data?.split_pct ? plain(data.split_pct) : '')

  const preview = (() => {
    try {
      const parsed = parseAmount(gross)
      return parsed && shownSplit ? netOf(parsed, shownSplit) : null
    } catch {
      return null // not an amount yet: nothing to show
    }
  })()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    let grossValue: string | null
    let feeValue: string | null
    try {
      grossValue = parseAmount(gross)
      feeValue = parseAmount(fee)
    } catch (error) {
      setProblem(messageOf(error))
      return
    }
    if (grossValue === null) {
      setProblem('Type the gross amount: what the firm paid out of the account.')
      return
    }
    if (!shownSplit) {
      setProblem("Type your split: this account's rules give none.")
      return
    }
    setProblem(null)
    add.mutate(
      { entry_date: date, gross: grossValue, split_pct: shownSplit, fee: feeValue, note },
      {
        onSuccess: () => {
          setGross('')
          setFee('')
          setNote('')
          setSplit(null)
        },
      },
    )
  }

  return (
    <div className="flex flex-col gap-3.5">
      {payouts.isPending ? <Spinner label="Loading the payouts" /> : null}
      {payouts.isError ? (
        <p role="alert" className="m-0">
          The payouts could not be loaded.
        </p>
      ) : null}
      {data ? (
        <div className="flex flex-wrap gap-x-8 gap-y-2" role="group" aria-label="Lifetime payouts">
          <div>
            <div className="text-xs text-muted">Withdrawn (gross)</div>
            <div className="num text-lg font-semibold">{formatMoney(data.lifetime_gross)}</div>
          </div>
          <div>
            <div className="text-xs text-muted">Received (net)</div>
            <div className="num text-lg font-semibold text-pos">{formatMoney(data.lifetime_net)}</div>
          </div>
          {data.lifetime_fees !== '0.00' ? (
            <div>
              <div className="text-xs text-muted">Fees</div>
              <div className="num text-lg font-semibold">{formatMoney(data.lifetime_fees)}</div>
            </div>
          ) : null}
        </div>
      ) : null}
      {data && data.payouts.length === 0 ? <p className="m-0 text-muted">No payouts yet.</p> : null}
      {data && data.payouts.length > 0 ? (
        <div className="scroll-x">
          <table className="w-full border-collapse text-[13px]" aria-label="Payouts">
            <thead>
              <tr>
                <th scope="col" className={`${HEAD} text-left`}>
                  #
                </th>
                <th scope="col" className={`${HEAD} text-left`}>
                  Date
                </th>
                <th scope="col" className={`${HEAD} text-right`}>
                  Gross
                </th>
                <th scope="col" className={`${HEAD} text-right`}>
                  Split
                </th>
                <th scope="col" className={`${HEAD} text-right`}>
                  Net
                </th>
                <th scope="col" className={`${HEAD} text-right`}>
                  Fee
                </th>
                <th scope="col" className={`${HEAD} text-left`}>
                  Note
                </th>
              </tr>
            </thead>
            <tbody>
              {data.payouts.map((entry) => (
                <PayoutRow key={entry.id} entry={entry} />
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row" colSpan={2} className="px-3 py-2 text-left text-xs font-medium text-muted">
                  Lifetime
                </th>
                <td className="num px-3 py-2 text-right font-semibold">{formatMoney(data.lifetime_gross)}</td>
                <td />
                <td className="num px-3 py-2 text-right font-semibold">{formatMoney(data.lifetime_net)}</td>
                <td className="num px-3 py-2 text-right">
                  {data.lifetime_fees !== '0.00' ? formatMoney(data.lifetime_fees) : '—'}
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      ) : null}

      <form onSubmit={submit} aria-label="Log a payout" className="flex flex-col gap-3 border-t border-line pt-3.5">
        <h3 className="m-0 text-sm font-semibold">Log payout #{data?.next_number ?? 1}</h3>
        <div className="flex flex-wrap gap-3">
          <div className="w-40">
            <Field id="payout-date" label="Date">
              <input
                id="payout-date"
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className={CONTROL}
              />
            </Field>
          </div>
          <div className="w-36">
            <Field id="payout-gross" label="Gross ($)" hint="What the firm paid out.">
              <input
                id="payout-gross"
                inputMode="decimal"
                value={gross}
                onChange={(e) => setGross(e.target.value)}
                className={`${CONTROL} num`}
              />
            </Field>
          </div>
          <div className="w-28">
            <Field
              id="payout-split"
              label="Your split (%)"
              hint={data?.split_pct ? 'From the rules.' : 'The rules give none.'}
            >
              <input
                id="payout-split"
                inputMode="decimal"
                value={shownSplit}
                onChange={(e) => setSplit(e.target.value)}
                className={`${CONTROL} num`}
              />
            </Field>
          </div>
          <div className="w-28">
            <Field id="payout-fee" label="Fee ($)" hint="Optional.">
              <input
                id="payout-fee"
                inputMode="decimal"
                value={fee}
                onChange={(e) => setFee(e.target.value)}
                className={`${CONTROL} num`}
              />
            </Field>
          </div>
        </div>
        <p className="m-0 text-[13px]" role="status" aria-live="polite">
          {preview ? (
            <>
              You receive <span className="num font-semibold text-pos">{formatMoney(preview)}</span> before fees.
            </>
          ) : (
            <span className="text-muted">Type the gross and your split to see the net.</span>
          )}
        </p>
        <Field id="payout-note" label="Note (optional)">
          <input id="payout-note" value={note} onChange={(e) => setNote(e.target.value)} className={CONTROL} />
        </Field>
        {problem || add.isError ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {problem ?? messageOf(add.error)}
          </p>
        ) : null}
        <div>
          <Button type="submit" variant="primary" disabled={add.isPending}>
            {add.isPending ? 'Logging…' : 'Log payout'}
          </Button>
        </div>
      </form>
    </div>
  )
}
