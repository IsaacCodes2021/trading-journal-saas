import { Card, Pnl, Spinner } from '../../components/ui/primitives'
import { useBusinessTotals } from '../../lib/data/queries'
import type { ReactNode } from 'react'
import { formatPercent, formatPnl, pluralize } from '../../lib/format'

function Figure({ label, note, children }: { label: string; note: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="num text-[22px] font-semibold">{children}</div>
      <div className="text-xs text-muted">{note}</div>
    </div>
  )
}

/** Is the prop business itself profitable? Evaluation pass rate, what was spent, what was paid out, and the difference. */
export function BusinessCard() {
  const totals = useBusinessTotals()
  const data = totals.data
  const missing = data?.accounts_without_cost ?? []
  return (
    <Card className="px-5 py-4" role="region" aria-label="Prop business">
      <h2 className="m-0 mb-3 text-base font-semibold">Prop business</h2>
      {totals.isPending ? <Spinner label="Loading" /> : null}
      {totals.isError ? (
        <p role="alert" className="m-0">
          The totals could not be loaded.
        </p>
      ) : null}
      {data ? (
        <>
          <div className="flex flex-wrap gap-x-10 gap-y-5">
            <Figure
              label="Evaluation pass rate"
              note={`${data.evaluations_passed} of ${pluralize(data.evaluations_passed + data.evaluations_failed, 'evaluation')} completed`}
            >
              {data.pass_rate === null ? '—' : formatPercent(data.pass_rate, 0)}
            </Figure>
            <Figure label="Costs" note="fees, resets, data">
              <span className="text-neg">{formatPnl(`-${data.costs}`)}</span>
            </Figure>
            <Figure label="Payouts" note="received">
              <Pnl value={data.payouts} />
            </Figure>
            <Figure label="Net business" note="payouts minus costs">
              <Pnl value={data.net} />
            </Figure>
          </div>
          {missing.length > 0 ? (
            <p className="mt-3 mb-0 text-xs text-muted">
              {pluralize(missing.length, 'account')} {missing.length === 1 ? 'has' : 'have'} no cost recorded (
              {missing.slice(0, 3).join(', ')}
              {missing.length > 3 ? ` and ${missing.length - 3} more` : ''}). Open one and add its fee in Ledger so the
              net is true.
            </p>
          ) : null}
        </>
      ) : null}
    </Card>
  )
}
