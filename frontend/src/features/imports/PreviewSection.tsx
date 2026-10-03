import { Button, Card, Pnl } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { ImportPreview, PairStatus } from '../../lib/data/model'
import { formatPrice, pluralize } from '../../lib/format'

const PILL: Record<PairStatus, string> = {
  new: 'bg-long-bg text-pos',
  duplicate: 'bg-raised text-muted',
  other_account: 'bg-warn/15 text-warn',
}

function Pill({ status, account }: { status: PairStatus; account: string | null }) {
  const text = status === 'new' ? 'New' : status === 'duplicate' ? 'Already imported' : `Belongs to ${account}`
  return (
    <span className={cx('inline-flex h-[22px] items-center rounded-md px-2 text-xs font-medium', PILL[status])}>
      {text}
    </span>
  )
}

function Tile({ label, value, tone }: { label: string; value: number; tone?: 'pos' | 'warn' }) {
  return (
    <Card className="min-w-[140px] flex-[1_1_150px] px-4 py-3.5">
      <div className="text-xs text-muted">{label}</div>
      <div
        className={cx(
          'num mt-0.5 text-2xl font-semibold',
          tone === 'pos' && value > 0 && 'text-pos',
          tone === 'warn' && value > 0 && 'text-warn',
        )}
      >
        {value.toLocaleString('en-US')}
      </div>
    </Card>
  )
}

const th = 'border-b border-line px-3.5 py-2.5 text-xs font-medium whitespace-nowrap text-muted'
const td = 'num border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap'
const SHOWN_ERRORS = 20

/** What importing the chosen file would do: the counts, any rows that cannot be read, and the rows themselves. */
export function PreviewSection({
  preview,
  accountName,
  committing,
  onCancel,
  onCommit,
}: {
  preview: ImportPreview
  accountName: string
  committing: boolean
  onCancel: () => void
  onCommit: () => void
}) {
  const skippedElsewhere = Object.values(preview.duplicates_other_accounts).reduce((a, b) => a + b, 0)
  const already = preview.duplicates + preview.duplicates_in_file
  const inFile = preview.rows_parsed + preview.duplicates_in_file + preview.errors.length
  const hidden = preview.rows_parsed - preview.rows.length
  return (
    <>
      <section aria-label="Preview summary" className="flex flex-wrap gap-3">
        <Tile label="Rows in file" value={inFile} />
        <Tile label="New fill pairs" value={preview.rows_added} tone="pos" />
        <Tile label="Already imported" value={already} />
        <Tile label="Skipped (other account)" value={skippedElsewhere} tone="warn" />
        <Tile label="Errors" value={preview.errors.length} />
        <Tile label="Resulting trades" value={preview.trades_built} />
      </section>

      {preview.errors.length > 0 ? (
        <Card className="px-5 py-4" role="region" aria-label="Rows that cannot be imported">
          <h2 className="m-0 text-base font-semibold">{pluralize(preview.errors.length, 'row')} cannot be imported</h2>
          <p className="mt-1 mb-2 text-muted">
            The rest of the file can be. Fix these in the file and import it again.
          </p>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-[13px]">
            {preview.errors.slice(0, SHOWN_ERRORS).map((issue) => (
              <li key={issue.line}>
                <span className="num text-muted">Line {issue.line}:</span> {issue.message}
              </li>
            ))}
          </ul>
          {preview.errors.length > SHOWN_ERRORS ? (
            <p className="mt-2 mb-0 text-xs text-muted">and {preview.errors.length - SHOWN_ERRORS} more</p>
          ) : null}
        </Card>
      ) : null}

      <Card className="p-0" role="region" aria-label="Preview">
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3.5">
          <h2 className="m-0 text-base font-semibold">Preview</h2>
          <span className="text-[13px] text-muted">Dry run: nothing is saved until you confirm</span>
          <span className="grow" />
          <Button onClick={onCancel} disabled={committing}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onCommit} disabled={committing || preview.rows_added === 0}>
            {committing ? 'Importing…' : `Import ${pluralize(preview.rows_added, 'fill pair')}`}
          </Button>
        </div>
        {preview.rows_added === 0 ? (
          <p role="status" className="m-0 border-b border-line px-5 py-3 text-warn">
            Nothing new to import into {accountName}: every row is already stored.
          </p>
        ) : null}
        <div className="scroll-x">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th scope="col" className={`${th} text-left`}>
                  Status
                </th>
                <th scope="col" className={`${th} text-left`}>
                  Symbol
                </th>
                <th scope="col" className={`${th} text-left`}>
                  Bought
                </th>
                <th scope="col" className={`${th} text-left`}>
                  Sold
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Qty
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Buy
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Sell
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Gross P&amp;L
                </th>
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((row) => (
                <tr key={row.line}>
                  <td className="border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap">
                    <Pill status={row.status} account={row.account} />
                  </td>
                  <td className={td}>{row.symbol}</td>
                  <td className={td}>{row.bought_at}</td>
                  <td className={td}>{row.sold_at}</td>
                  <td className={`${td} text-right`}>{row.qty}</td>
                  <td className={`${td} text-right`}>{formatPrice(row.buy_price)}</td>
                  <td className={`${td} text-right`}>{formatPrice(row.sell_price)}</td>
                  <td className="border-b border-line-soft px-3.5 py-2.5 text-right whitespace-nowrap">
                    <Pnl value={row.pnl} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {hidden > 0 ? (
          <p className="m-0 px-5 py-3 text-xs text-muted">
            Showing the first {preview.rows.length} of {preview.rows_parsed.toLocaleString('en-US')} rows. All of them
            are imported.
          </p>
        ) : null}
      </Card>
    </>
  )
}
