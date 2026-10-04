import { Link, useNavigate } from '@tanstack/react-router'
import type { ImportRecord } from '../../lib/data/model'
import { pluralize } from '../../lib/format'
import { StepFrame } from './StepFrame'

const LINK =
  'inline-flex h-9 items-center justify-center rounded-lg border border-line bg-raised px-3 text-ink no-underline hover:border-line-strong hover:text-ink'

/** Step 5: what was done, and where to go next. Continue opens the dashboard. */
export function DoneStep({ imported, onBack }: { imported: ImportRecord | null; onBack: () => void }) {
  const navigate = useNavigate()
  return (
    <StepFrame
      number={5}
      title="You are all set"
      intro={
        imported
          ? `Your first file is in: ${pluralize(imported.rows_added, 'fill pair')} from ${imported.file_name} became ${pluralize(imported.trades_built, 'trade')} in ${imported.account.name}.`
          : 'Your timezone and account are saved. You have not imported a file yet; you can do that whenever you are ready.'
      }
      onBack={onBack}
      next={{ label: 'Open my dashboard', onClick: () => void navigate({ to: '/' }) }}
    >
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
        <li>
          <Link to="/import" className={LINK}>
            {imported ? 'Import another file' : 'Import a file'}
          </Link>
        </li>
        <li>
          <Link to="/settings/accounts" className={LINK}>
            Set up account rules
          </Link>
        </li>
        <li>
          <Link to="/settings/preferences" className={LINK}>
            Preferences
          </Link>
        </li>
      </ul>
      <p className="m-0 text-[13px] text-muted">You can change any of this later in Settings.</p>
    </StepFrame>
  )
}
