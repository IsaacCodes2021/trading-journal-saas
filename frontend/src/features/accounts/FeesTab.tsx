import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Spinner } from '../../components/ui/primitives'
import type { FeeSchedule } from '../../lib/data/model'
import { useFees, useRemoveFee, useSetFee } from '../../lib/data/queries'
import { parseAmount } from '../../lib/money'

const ANY_OTHER = '*'

function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'That did not work. Nothing was changed.'
}

function FeeRow({ accountId, root, rate }: { accountId: number; root: string; rate: string }) {
  const set = useSetFee(accountId)
  const remove = useRemoveFee(accountId)
  const [text, setText] = useState(rate)
  const [problem, setProblem] = useState<string | null>(null)
  const label = root === ANY_OTHER ? 'any other contract' : root

  const save = () => {
    let parsed: string | null
    try {
      parsed = parseAmount(text)
    } catch (error) {
      setProblem(messageOf(error))
      return
    }
    if (parsed === null) {
      setProblem('Type a fee, or remove the line.')
      return
    }
    setProblem(null)
    if (parsed !== rate) set.mutate({ root, rate: parsed })
  }

  return (
    <li className="flex flex-wrap items-center gap-2 border-b border-line-soft py-2">
      <span className="num w-28 shrink-0">{root === ANY_OTHER ? 'Any other' : root}</span>
      <div className="flex items-center gap-1">
        <span aria-hidden="true" className="text-muted">
          $
        </span>
        <input
          aria-label={`Fee for ${label}`}
          inputMode="decimal"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              save()
            }
          }}
          className={`${CONTROL} num !h-9 !w-24`}
        />
      </div>
      <Button
        className="h-[30px]"
        aria-label={`Remove the fee for ${label}`}
        disabled={remove.isPending}
        onClick={() => remove.mutate(root)}
      >
        Remove
      </Button>
      {problem || set.isError ? (
        <span role="alert" className="text-xs text-neg">
          {problem ?? messageOf(set.error)}
        </span>
      ) : null}
    </li>
  )
}

/** What one contract costs round trip, by symbol: the part of the net result that is the firm's and broker's. */
export function FeesTab({ accountId }: { accountId: number }) {
  const fees = useFees(accountId)
  const set = useSetFee(accountId)
  const [root, setRoot] = useState('')
  const [rate, setRate] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const schedule: FeeSchedule = fees.data ?? {}
  const rows = Object.entries(schedule).sort(([a], [b]) =>
    a === ANY_OTHER ? 1 : b === ANY_OTHER ? -1 : a.localeCompare(b),
  )

  const add = (event: FormEvent) => {
    event.preventDefault()
    let parsed: string | null
    try {
      parsed = parseAmount(rate)
    } catch (error) {
      setProblem(messageOf(error))
      return
    }
    if (parsed === null) {
      setProblem('Type the fee for one contract.')
      return
    }
    setProblem(null)
    set.mutate(
      { root: root.trim().toUpperCase() || ANY_OTHER, rate: parsed },
      {
        onSuccess: () => {
          setRoot('')
          setRate('')
        },
      },
    )
  }

  return (
    <div className="flex flex-col gap-3.5">
      <p className="m-0 text-[13px] text-muted">
        The fee for one contract, round trip (in and out), by contract. Changing a fee changes the net result of the
        trades it applies to. A line for &ldquo;any other contract&rdquo; covers the rest.
      </p>
      {fees.isPending ? <Spinner label="Loading the fees" /> : null}
      {fees.isError ? (
        <p role="alert" className="m-0">
          The fees could not be loaded.
        </p>
      ) : null}
      {fees.data && rows.length === 0 ? (
        <p className="m-0 text-muted">No fees set: trades on this account show no fees.</p>
      ) : null}
      {rows.length > 0 ? (
        <ul className="m-0 list-none p-0" aria-label="Fees by contract">
          {rows.map(([symbol, value]) => (
            <FeeRow key={`${symbol}:${value}`} accountId={accountId} root={symbol} rate={value} />
          ))}
        </ul>
      ) : null}
      <form onSubmit={add} className="flex flex-wrap items-end gap-2" aria-label="Add a fee">
        <div className="w-28">
          <Field id="fee-root" label="Contract" hint="like NQ">
            <input
              id="fee-root"
              value={root}
              onChange={(e) => setRoot(e.target.value)}
              placeholder="NQ"
              className={CONTROL}
            />
          </Field>
        </div>
        <div className="w-28">
          <Field id="fee-rate" label="Fee ($)" hint="per contract">
            <input
              id="fee-rate"
              inputMode="decimal"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              className={`${CONTROL} num`}
            />
          </Field>
        </div>
        <Button type="submit" disabled={set.isPending} className="mb-[34px]">
          Add fee
        </Button>
      </form>
      {problem || (set.isError && set.variables?.root === (root.trim().toUpperCase() || ANY_OTHER)) ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {problem ?? messageOf(set.error)}
        </p>
      ) : null}
    </div>
  )
}
