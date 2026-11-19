import Decimal from 'decimal.js'
import type { ReactNode } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Card, Pnl } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { CommissionCheck, ImportPreview, PairStatus } from '../../lib/data/model'
import { useSetFee } from '../../lib/data/queries'
import { formatLongDay, formatPrice, pluralize } from '../../lib/format'
import { formatMoney } from '../../lib/money'
import { checkStatement, statementProblem } from '../../lib/otherPlatform'
import { platformInfo, TRADESEA_PAGE_ROWS } from '../../lib/platforms'

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
const SHOWN_WARNINGS = 20

const total = (counts: Record<string, number>) => Object.values(counts).reduce((a, b) => a + b, 0)

/** "3 cancelled orders and 1 rejected order". */
function byStatus(counts: Record<string, number>): string {
  const parts = Object.entries(counts).map(([status, n]) => pluralize(n, `${status} order`))
  return parts.length <= 1 ? (parts[0] ?? '') : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

function Note({ tone, children }: { tone?: 'warn'; children: ReactNode }) {
  return (
    <li className={cx('flex flex-wrap items-center gap-x-3 gap-y-1.5', tone === 'warn' && 'text-warn')}>{children}</li>
  )
}

/** A contract whose commission in the files differs from the account's own setting, and a button to use the files'. */
function CommissionNote({
  check,
  log,
  file,
  accountId,
  accountName,
  onSet,
}: {
  check: CommissionCheck
  /** The Round Turn Monitor's log: its rate is the usual one its rows show, not its total over the contracts. */
  log: boolean
  /** Roundturn's own import file (Other platform): its rate is the usual one its rows show, too. */
  file: boolean
  accountId: number
  accountName: string
  onSet: () => void
}) {
  const setFee = useSetFee(accountId)
  const rate = formatMoney(check.file_rate)
  const button = (
    <Button
      className="h-[30px]"
      disabled={setFee.isPending}
      onClick={() => setFee.mutate({ root: check.root, rate: check.file_rate }, { onSuccess: onSet })}
    >
      Use {rate} per contract for {check.root}
    </Button>
  )
  const free = new Decimal(check.schedule_rate).isZero()
  return (
    <Note tone={free ? 'warn' : undefined}>
      <span>
        {free && log ? (
          <>
            Your commission setting for {check.root} on {accountName} is $0.00, but the Round Turn Monitor charges{' '}
            {rate} per contract round trip. Roundturn uses your setting, so your net P&amp;L would be too high.
          </>
        ) : free && file ? (
          <>
            Your commission setting for {check.root} on {accountName} is $0.00, but the file&rsquo;s fees are {rate} per
            contract round trip. Roundturn uses your setting, so your net P&amp;L would be too high.
          </>
        ) : free ? (
          <>
            Your commission setting for {check.root} on {accountName} is $0.00, but these files show{' '}
            {formatMoney(check.file_total)} in commissions. Roundturn uses your setting, so your net P&amp;L would be{' '}
            {formatMoney(check.file_total)} too high.
          </>
        ) : log ? (
          <>
            The Round Turn Monitor charges {rate} per contract round trip for {check.root}. Your setting on{' '}
            {accountName} charges {formatMoney(check.schedule_rate)} per contract, and Roundturn uses your setting.
          </>
        ) : file ? (
          <>
            The file&rsquo;s fees for {check.root} are {rate} per contract round trip. Your setting on {accountName}{' '}
            charges {formatMoney(check.schedule_rate)} per contract, and Roundturn uses your setting.
          </>
        ) : (
          <>
            These files&rsquo; commissions for {check.root} come to {formatMoney(check.file_total)} ({rate} per contract
            round trip). Your setting on {accountName} charges {formatMoney(check.schedule_total)} (
            {formatMoney(check.schedule_rate)} per contract), and Roundturn uses your setting.
          </>
        )}
      </span>
      {button}
      {setFee.isError ? (
        <span role="alert" className="text-neg">
          The setting could not be changed.
        </span>
      ) : null}
    </Note>
  )
}

/** What a TradeSea, Round Turn Monitor or Other platform preview adds to the counts: pages, orders that did not fill,
 * positions, commissions, other accounts. */
function OrderNotes({
  preview,
  accountId,
  accountName,
  onAddFiles,
  onRecheck,
}: {
  preview: ImportPreview
  accountId: number
  accountName: string
  onAddFiles: () => void
  onRecheck: (done: string) => void
}) {
  const log = preview.platform === 'round_turn_monitor'
  const file = preview.platform === 'other' // Roundturn's own import file
  const skipped = total(preview.skipped_by_status)
  const elsewhere = Object.entries(preview.matches_other_accounts)
  const differing = preview.commissions.filter((c) => !new Decimal(c.file_rate).equals(c.schedule_rate))
  const shownWarnings = preview.warnings.slice(0, SHOWN_WARNINGS)
  return (
    <Card className="px-5 py-4" role="region" aria-label="Things to check">
      <h2 className="m-0 mb-2 text-base font-semibold">Things to check</h2>
      <ul className="m-0 flex list-none flex-col gap-2 p-0 text-[13px]">
        {preview.one_page ? (
          <Note tone="warn">
            <span>
              This looks like one page of TradeSea&rsquo;s list. If you had more than {TRADESEA_PAGE_ROWS} filled
              orders, export the other pages and add them.
            </span>
            <Button className="h-[30px]" onClick={onAddFiles}>
              Add files
            </Button>
          </Note>
        ) : null}
        {preview.open_positions.map((position) => (
          <Note key={`${position.symbol}-${position.direction}`} tone="warn">
            <span>
              These orders end with{' '}
              <strong>
                {position.qty} {position.symbol} {position.direction} still open
              </strong>{' '}
              (opened <span className="num">{position.since}</span>).{' '}
              {log ? (
                <>
                  A round turn is missing its other half. Choose the whole log, with its overflow file if there is one.
                </>
              ) : file ? (
                <>A round turn is missing its other half. Check the file&rsquo;s rows for {position.symbol}.</>
              ) : (
                <>
                  If you were flat, TradeSea&rsquo;s export left out an order: show the whole day in TradeSea&rsquo;s
                  Orders list and export every page again. If you are still in the trade, import it with its exit later.
                </>
              )}
            </span>
          </Note>
        ))}
        {preview.trades_removed > 0 ? (
          <Note tone="warn">
            <span>
              {pluralize(preview.trades_removed, 'trade')} you already have would be removed: their orders are now an
              open position. Their notes, tags and screenshots go with them.
            </span>
          </Note>
        ) : null}
        {skipped > 0 ? (
          <Note>
            <span>{byStatus(preview.skipped_by_status)} were skipped: they did not fill.</span>
          </Note>
        ) : null}
        {preview.duplicates_in_file > 0 ? (
          <Note>
            <span>
              {log
                ? `${pluralize(preview.duplicates_in_file, 'round turn is', 'round turns are')} in more than one file; each counts once.`
                : `${pluralize(preview.duplicates_in_file, 'order is', 'orders are')} on more than one page; each counts once.`}
            </span>
          </Note>
        ) : null}
        {preview.trades_changed > 0 ? (
          <Note>
            <span>
              {pluralize(preview.trades_changed, 'trade')} you already have change: their orders are paired again, and
              they keep their notes, tags and screenshots.
            </span>
          </Note>
        ) : null}
        {preview.file_commission !== null && log ? (
          <Note>
            <span>
              The log&rsquo;s own commissions for these round turns add up to {formatMoney(preview.file_commission)}.
              Roundturn charges your commission setting on every trade instead.
            </span>
          </Note>
        ) : null}
        {preview.file_commission !== null && file ? (
          <Note>
            <span>
              The file&rsquo;s own fees for these round turns add up to {formatMoney(preview.file_commission)}.
              Roundturn charges your commission setting on every trade instead.
            </span>
          </Note>
        ) : null}
        {preview.file_commission !== null && !log && !file ? (
          <Note>
            <span>
              These files&rsquo; commissions add up to {formatMoney(preview.file_commission)}. If TradeSea&rsquo;s Total
              Charges for the day shows the same, no fill is missing.
            </span>
          </Note>
        ) : null}
        {differing.map((check) => (
          <CommissionNote
            key={check.root}
            check={check}
            log={log}
            file={file}
            accountId={accountId}
            accountName={accountName}
            onSet={() =>
              onRecheck(
                `${accountName} now charges ${formatMoney(check.file_rate)} per contract for ${check.root}, and its stored trades were recalculated.`,
              )
            }
          />
        ))}
        {elsewhere.map(([name, count]) =>
          log || file ? (
            <Note key={name} tone="warn">
              <span>
                {count === preview.rows_parsed
                  ? `These ${pluralize(count, 'round turn')} are`
                  : pluralize(count, 'round turn is', 'round turns are')}{' '}
                already in {name}. Check that you chose the right {log ? 'NinjaTrader account' : 'account'}.
              </span>
            </Note>
          ) : (
            <Note key={name}>
              <span>
                {count === preview.rows_parsed ? `These ${pluralize(count, 'order')}` : pluralize(count, 'order')}{' '}
                {count === 1 ? 'matches an order' : 'match orders'} in {name}. That is normal if you copy trades;
                otherwise check the account.
              </span>
            </Note>
          ),
        )}
        {shownWarnings.map((issue, index) => (
          <Note key={`${issue.line}-${index}`}>
            <span>
              {issue.line > 0 ? <span className="num text-muted">Line {issue.line}: </span> : null}
              {issue.message}
            </span>
          </Note>
        ))}
        {preview.warnings.length > SHOWN_WARNINGS ? (
          <li className="text-xs text-muted">and {preview.warnings.length - SHOWN_WARNINGS} more</li>
        ) : null}
      </ul>
    </Card>
  )
}

/**
 * Roundturn's own import file: what it holds, and its net P&L against the trader's own statement, which they typed when
 * they chose Other platform (and can change here). The import waits for a figure: it is how a chatbot's mistake in the
 * file is caught before it reaches the journal.
 */
function StatementCheckCard({
  preview,
  statement,
  onStatement,
}: {
  preview: ImportPreview
  statement: string
  onStatement: (text: string) => void
}) {
  const net = preview.file_net ?? '0.00'
  const check = checkStatement(net, statement)
  const days =
    preview.first_day && preview.last_day
      ? preview.first_day === preview.last_day
        ? `on ${formatLongDay(preview.first_day)}`
        : `from ${formatLongDay(preview.first_day)} to ${formatLongDay(preview.last_day)}`
      : null
  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Check against your statement">
      <h2 className="m-0 text-base font-semibold">Check against your statement</h2>
      <p className="m-0 text-[13px]">
        The file has {pluralize(preview.rows_parsed, 'round turn')}
        {days ? ` ${days}` : ''}: gross <Pnl value={preview.file_gross ?? '0.00'} />, fees{' '}
        {formatMoney(preview.file_fees ?? '0.00')}, net <Pnl value={net} />.
        {preview.errors.length > 0
          ? ` The ${pluralize(preview.errors.length, 'row')} below that cannot be imported ${preview.errors.length === 1 ? 'is' : 'are'} not in these totals.`
          : ''}
      </p>
      <div className="max-w-[320px]">
        <Field id="statement-check" label="Net P&L on your platform’s statement" error={statementProblem(statement)}>
          <input
            id="statement-check"
            inputMode="decimal"
            autoComplete="off"
            placeholder="-125.50"
            value={statement}
            onChange={(event) => onStatement(event.target.value)}
            className={`${CONTROL} num`}
          />
        </Field>
      </div>
      <p role="status" className="m-0 text-[13px] empty:hidden">
        {check.state === 'empty' ? (
          <span className="text-muted">
            Type the net P&amp;L your platform&rsquo;s statement shows for the same dates. The import waits for it.
          </span>
        ) : check.state === 'match' ? (
          <span className="text-pos">The file&rsquo;s net P&amp;L matches your statement.</span>
        ) : check.state === 'differs' ? (
          <span className="text-warn">
            The file&rsquo;s net P&amp;L is {formatMoney(net)}, but your statement says {formatMoney(check.statement)}:{' '}
            {formatMoney(new Decimal(check.difference).abs().toFixed(2))} apart. A trade may be missing, doubled or
            wrong in the file. Ask the chatbot to check it against your export row by row, or import it anyway if you
            know why they differ.
          </span>
        ) : null}
      </p>
    </Card>
  )
}

/** Whether a TradeSea preview has anything for "Things to check". */
function hasOrderNotes(preview: ImportPreview): boolean {
  return (
    preview.one_page ||
    preview.open_positions.length > 0 ||
    preview.trades_removed > 0 ||
    preview.trades_changed > 0 ||
    preview.duplicates_in_file > 0 ||
    total(preview.skipped_by_status) > 0 ||
    preview.file_commission !== null ||
    Object.keys(preview.matches_other_accounts).length > 0 ||
    preview.warnings.length > 0
  )
}

/**
 * What importing the chosen files would do: what was read, the counts, any rows that cannot be read, and the rows
 * themselves. For TradeSea, a row of the files is a filled order; the table shows the fill pairs the orders make, and
 * "Things to check" says what the counts cannot (a missing page, a position left open, commissions). For the Round Turn
 * Monitor, a row is a round turn of one NinjaTrader account (the log holds every account; the page picks one). For
 * Roundturn's own import file (Other platform), a row is a round turn, and the file's net P&L is checked against the
 * trader's statement (`statement`) before the import button works.
 */
export function PreviewSection({
  preview,
  accountId,
  accountName,
  committing,
  statement = '',
  onStatement = () => undefined,
  onCancel,
  onCommit,
  onAddFiles,
  onRecheck,
}: {
  preview: ImportPreview
  accountId: number
  accountName: string
  committing: boolean
  /** Other platform: the net P&L the trader typed from their statement, and a change to it. */
  statement?: string
  onStatement?: (text: string) => void
  onCancel: () => void
  onCommit: () => void
  /** Opens the file window to add more of the same platform's files (TradeSea's other pages). */
  onAddFiles: () => void
  /** Works the preview out again after the account's fee setting changed, saying so (`done`) above the form. */
  onRecheck: (done: string) => void
}) {
  const orders = preview.platform !== 'tradovate' // stored as orders and paired: TradeSea and the Round Turn Monitor
  const log = preview.platform === 'round_turn_monitor'
  const file = preview.platform === 'other' // Roundturn's own import file
  const roundTurns = log || file // a row of the file is a round turn
  const statementState = file ? checkStatement(preview.file_net ?? '0.00', statement).state : null
  const waitingForStatement = statementState === 'empty' || statementState === 'invalid'
  const info = platformInfo(preview.platform)
  const [one, many] = info.row
  const [readOne, readMany] = preview.platform === 'tradesea' ? ['filled order', 'filled orders'] : info.row
  const skippedElsewhere = total(preview.duplicates_other_accounts)
  const already = orders ? preview.duplicates : preview.duplicates + preview.duplicates_in_file
  const inFile = preview.rows_parsed + preview.duplicates_in_file + preview.errors.length
  const hidden = preview.rows_parsed - preview.rows.length
  return (
    <>
      <p className="m-0 text-[13px] text-muted" aria-label="What was read">
        <strong className="text-ink">{info.name}</strong>
        {preview.recognised ? ' (recognised from the file)' : ''}
        {preview.file_count > 1 ? ` · ${preview.file_count} files` : ''} ·{' '}
        {pluralize(preview.rows_parsed, readOne, readMany)}, {preview.rows_added.toLocaleString('en-US')} new
        {log && preview.log_account ? ` · from ${preview.log_account}` : ''} · into {accountName} · times in{' '}
        <span className="num">{preview.time_zone}</span>
      </p>

      <section aria-label="Preview summary" className="flex flex-wrap gap-3">
        {roundTurns ? (
          <>
            <Tile label="Round turns" value={preview.rows_parsed} />
            <Tile label="New round turns" value={preview.rows_added} tone="pos" />
            <Tile label="Already imported" value={already} />
          </>
        ) : orders ? (
          <>
            <Tile label="Filled orders" value={preview.rows_parsed} />
            <Tile label="New orders" value={preview.rows_added} tone="pos" />
            <Tile label="Already imported" value={already} />
            <Tile label="Not filled (skipped)" value={total(preview.skipped_by_status)} />
          </>
        ) : (
          <>
            <Tile label="Rows in file" value={inFile} />
            <Tile label="New fill pairs" value={preview.rows_added} tone="pos" />
            <Tile label="Already imported" value={already} />
            <Tile label="Skipped (other account)" value={skippedElsewhere} tone="warn" />
          </>
        )}
        <Tile label="Errors" value={preview.errors.length} />
        <Tile label="Resulting trades" value={preview.trades_built} />
      </section>

      {file ? <StatementCheckCard preview={preview} statement={statement} onStatement={onStatement} /> : null}

      {orders && hasOrderNotes(preview) ? (
        <OrderNotes
          preview={preview}
          accountId={accountId}
          accountName={accountName}
          onAddFiles={onAddFiles}
          onRecheck={onRecheck}
        />
      ) : null}

      {preview.errors.length > 0 ? (
        <Card className="px-5 py-4" role="region" aria-label="Rows that cannot be imported">
          <h2 className="m-0 text-base font-semibold">{pluralize(preview.errors.length, 'row')} cannot be imported</h2>
          <p className="mt-1 mb-2 text-muted">
            The rest {orders && preview.file_count > 1 ? 'of the files' : 'of the file'} can be. Fix these and import
            again.
          </p>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-[13px]">
            {preview.errors.slice(0, SHOWN_ERRORS).map((issue, index) => (
              <li key={`${issue.line}-${index}`}>
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
          <span className="text-[13px] text-muted">
            {roundTurns ? 'One row per round turn. ' : orders ? 'The fill pairs these orders make. ' : ''}Dry run:
            nothing is saved until you confirm
          </span>
          <span className="grow" />
          <Button onClick={onCancel} disabled={committing}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={onCommit}
            disabled={committing || preview.rows_added === 0 || waitingForStatement}
            title={waitingForStatement ? 'Type the net P&L from your statement first' : undefined}
          >
            {committing ? 'Importing…' : `Import ${pluralize(preview.rows_added, one, many)}`}
          </Button>
        </div>
        {preview.rows_added === 0 ? (
          <p role="status" className="m-0 border-b border-line px-5 py-3 text-warn">
            Nothing new to import into {accountName}: every {roundTurns ? 'round turn' : orders ? 'order' : 'row'} is
            already stored.
          </p>
        ) : null}
        {orders && preview.rows.length === 0 ? (
          <p className="m-0 border-b border-line px-5 py-3 text-muted">
            {log
              ? 'This log has no round turns for this account.'
              : file
                ? 'No round turn in the file can be imported.'
                : 'These orders close no trade yet.'}
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
              {preview.rows.map((row, index) => (
                <tr key={`${row.line}-${index}`}>
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
        {orders && preview.rows.length >= preview.rows_shown_limit ? (
          <p className="m-0 px-5 py-3 text-xs text-muted">
            Showing the first {preview.rows.length} {roundTurns ? 'round turns' : 'fill pairs'}. All of them are
            imported.
          </p>
        ) : null}
        {!orders && hidden > 0 ? (
          <p className="m-0 px-5 py-3 text-xs text-muted">
            Showing the first {preview.rows.length} of {preview.rows_parsed.toLocaleString('en-US')} rows. All of them
            are imported.
          </p>
        ) : null}
      </Card>
    </>
  )
}
