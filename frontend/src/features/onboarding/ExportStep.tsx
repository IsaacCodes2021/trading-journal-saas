import { REQUIRED_COLUMNS } from '../../lib/tradovate'
import { StepFrame } from './StepFrame'

/**
 * Step 3: how to get the file. The click path in Tradovate changes between versions, so this says what to look for (the
 * Performance report, as a CSV) and shows the columns the file must have, which is the one thing that can be checked:
 * the import tells the person exactly which are missing if the file does not match.
 */
export function ExportStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  return (
    <StepFrame
      number={3}
      title="Export from Tradovate"
      intro="We read the Performance report from Tradovate, saved as a CSV file. It lists every fill pair, which is what we build your trades from."
      onBack={onBack}
      next={{ onClick: onNext }}
    >
      <ol className="m-0 flex list-decimal flex-col gap-2 pl-5">
        <li>
          In Tradovate, find the <strong>Performance</strong> report for the account you trade (under its reports).
        </li>
        <li>Choose the dates you want: a day, a week, or everything you have.</li>
        <li>
          Export it as a <strong>CSV</strong> and save the file where you can find it.
        </li>
      </ol>

      <div className="flex flex-col gap-2">
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
          columns. Other exports (orders, statements) do not have them and cannot be read. If a file does not match, the
          next step says exactly what is missing. Nothing is saved until you confirm a preview.
        </p>
      </div>
    </StepFrame>
  )
}
