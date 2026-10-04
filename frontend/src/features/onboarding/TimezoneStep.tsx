import { useState } from 'react'
import { Field } from '../../components/ui/Field'
import { ApiError } from '../../lib/api/client'
import type { components } from '../../lib/api/schema'
import { useSaveSettings } from '../../lib/session'
import { timeZones } from '../../lib/timezones'
import { CsvZoneChoice } from '../settings/CsvZoneChoice'
import { ZoneSelect } from '../settings/ZoneSelect'
import { StepFrame } from './StepFrame'
import { detectedZone } from './steps'

type Settings = components['schemas']['SettingsOut']

/**
 * Step 1: the time zone everything is shown in, and which clock the Tradovate files use. Getting the second wrong lands
 * trades hours off and on the wrong trading day, so it is asked plainly and the consequence is said. The zone starts as
 * the one this browser reports, which is almost always right; nothing is saved until Continue.
 */
export function TimezoneStep({ settings, onNext }: { settings: Settings; onNext: () => void }) {
  const save = useSaveSettings()
  const detected = detectedZone()
  const [timezone, setTimezone] = useState(detected ?? settings.timezone)
  const [csv, setCsv] = useState<string | null>(settings.csv_timezone)
  const zones = timeZones([settings.timezone, timezone])

  const changes = {
    ...(timezone !== settings.timezone ? { timezone } : {}),
    ...(csv !== settings.csv_timezone ? { csv_timezone: csv ?? '' } : {}), // "" goes back to following the timezone
  }
  const problem = save.error instanceof ApiError ? save.error : null

  const next = () => {
    if (Object.keys(changes).length === 0) return onNext()
    save.mutate(changes, { onSuccess: onNext })
  }

  return (
    <StepFrame
      number={1}
      title="Confirm your timezone"
      intro="We show every time in your timezone and group trades into trading days with it. Your CSV files also carry times, and we need to know which clock they use."
      next={{ onClick: next, disabled: save.isPending, label: save.isPending ? 'Saving…' : 'Continue' }}
    >
      <Field
        id="welcome-timezone"
        label="Your timezone"
        hint={timezone === detected ? 'Detected from your browser.' : undefined}
        error={problem?.fields['timezone']}
      >
        <div className="max-w-[420px]">
          <ZoneSelect id="welcome-timezone" value={timezone} onChange={setTimezone} zones={zones} />
        </div>
      </Field>

      <CsvZoneChoice ownZone={timezone} value={csv} onChange={setCsv} />
      {problem?.fields['csv_timezone'] ? (
        <p role="alert" className="m-0 text-xs text-neg">
          {problem.fields['csv_timezone']}
        </p>
      ) : null}

      <div role="note" className="flex gap-3 rounded-lg border border-warn-line bg-bg px-3.5 py-3 text-[13px]">
        <span aria-hidden="true" className="font-semibold text-warn">
          !
        </span>
        <div>
          <strong className="text-warn">Why this matters.</strong> If this is wrong, trades can land hours off and on
          the wrong trading day, which skews daily P&amp;L and prop-account rules. If you get it wrong, you can fix it
          later in Settings.
        </div>
      </div>

      {save.isError && !problem ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          Could not save. Check your connection and try again.
        </p>
      ) : null}
      {problem && Object.keys(problem.fields).length === 0 ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {problem.message}
        </p>
      ) : null}
    </StepFrame>
  )
}
