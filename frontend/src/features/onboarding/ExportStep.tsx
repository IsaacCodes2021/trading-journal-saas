import { platformInfo, ROUND_TURN_LOG, ROUND_TURN_LOG_FOLDER, TRADESEA_PAGE_ROWS } from '../../lib/platforms'
import { REQUIRED_COLUMNS } from '../../lib/tradovate'
import { StepFrame } from './StepFrame'

/**
 * Step 3: how to get the files, from Tradovate, TradeSea or NinjaTrader's Round Turn Monitor (its log, which needs no
 * export), or from any other platform through an AI chatbot. Tradovate's click path changes between versions, so its part
 * says what to look for (the Performance report, as a CSV) and shows the columns the file must have, which is the one
 * thing that can be checked. TradeSea's says where its export is and that it saves one page of orders per file
 * (docs/design/tradesea-import-plan.md, section 1.9). Either way, the import says exactly what is wrong with a file.
 */
export function ExportStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  return (
    <StepFrame
      number={3}
      title="Export your trades"
      intro="We read Tradovate's Performance report, TradeSea's Orders export and the Round Turn Monitor's log from NinjaTrader, saved as CSV files, and any other platform's export through an AI chatbot. Use the part for the platform you trade on."
      onBack={onBack}
      next={{ onClick: onNext }}
    >
      <section aria-labelledby="export-tradovate" className="flex flex-col gap-2">
        <h3 id="export-tradovate" className="m-0 text-sm font-semibold">
          Tradovate
        </h3>
        <ol className="m-0 flex list-decimal flex-col gap-2 pl-5">
          <li>
            In Tradovate, find the <strong>Performance</strong> report for the account you trade (under its reports).
          </li>
          <li>Choose the dates you want: a day, a week, or everything you have.</li>
          <li>
            Export it as a <strong>CSV</strong> and save the file where you can find it.
          </li>
        </ol>
        <div className="text-[13px] font-medium">The file must have these columns</div>
        <ul aria-label="Required columns" className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          {REQUIRED_COLUMNS.map((column) => (
            <li key={column} className="num rounded-md border border-line bg-bg px-2 py-0.5 text-xs">
              {column}
            </li>
          ))}
        </ul>
        <p className="m-0 text-xs text-muted">
          Menu names change between Tradovate versions, so if you cannot find the report, look for a file with these
          columns. Other exports (orders, statements) do not have them and cannot be read.
        </p>
      </section>

      <section aria-labelledby="export-tradesea" className="flex flex-col gap-2">
        <h3 id="export-tradesea" className="m-0 text-sm font-semibold">
          TradeSea
        </h3>
        <ol className="m-0 flex list-decimal flex-col gap-2 pl-5">
          <li>
            In TradeSea, open <strong>Orders</strong> and select <strong>Filled</strong>.
          </li>
          <li>
            Export. TradeSea saves the page on screen ({TRADESEA_PAGE_ROWS} orders), so{' '}
            <strong>export every page</strong>, one right after another.
          </li>
          <li>In the next step, choose all the files together: they are read as one import.</li>
        </ol>
        <p className="m-0 text-xs text-muted">
          Export each account on its own.{' '}
          <a href={platformInfo('tradesea').guide} target="_blank" rel="noopener noreferrer">
            How to export from TradeSea
          </a>
        </p>
      </section>

      <section aria-labelledby="export-round-turn-monitor" className="flex flex-col gap-2">
        <h3 id="export-round-turn-monitor" className="m-0 text-sm font-semibold">
          NinjaTrader (Round Turn Monitor)
        </h3>
        <p className="m-0">
          Click <strong>Export</strong> in the add-on&rsquo;s window, or use the log it writes after every round turn.
          In the next step, choose the Export file, or <strong>{ROUND_TURN_LOG}</strong> in{' '}
          {ROUND_TURN_LOG_FOLDER.join(' › ')}.
        </p>
        <p className="m-0 text-xs text-muted">
          <a href={platformInfo('round_turn_monitor').guide} target="_blank" rel="noopener noreferrer">
            How to import from Round Turn Monitor
          </a>
        </p>
      </section>

      <section aria-labelledby="export-other" className="flex flex-col gap-2">
        <h3 id="export-other" className="m-0 text-sm font-semibold">
          Any other platform
        </h3>
        <p className="m-0">
          Export your trades from your platform as it allows. In the next step, choose{' '}
          <strong>{platformInfo('other').label}</strong>: it gives you a prompt that has ChatGPT, Claude or another AI
          chatbot turn the export into Roundturn&rsquo;s import file, which Roundturn checks against your
          statement&rsquo;s net P&amp;L.
        </p>
        <p className="m-0 text-xs text-muted">
          <a href={platformInfo('other').guide} target="_blank" rel="noopener noreferrer">
            How to import from another platform
          </a>
        </p>
      </section>

      <p className="m-0 text-xs text-muted">
        If a file does not match, the next step says exactly what is wrong. Nothing is saved until you confirm a
        preview.
      </p>
    </StepFrame>
  )
}
