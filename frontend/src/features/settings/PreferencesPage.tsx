import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Card, Segmented, Spinner } from '../../components/ui/primitives'
import { ApiError } from '../../lib/api/client'
import type { components } from '../../lib/api/schema'
import { useSaveSettings, useSession } from '../../lib/session'
import { applyTheme, type Theme } from '../../lib/theme'
import { isClockTime, timeZones } from '../../lib/timezones'
import { CsvZoneChoice } from './CsvZoneChoice'
import { ZoneSelect } from './ZoneSelect'
import { SectionHeader } from './SettingsLayout'

type Settings = components['schemas']['SettingsOut']

const THEMES = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
] as const

function Form({ saved }: { saved: Settings }) {
  const save = useSaveSettings()
  const [timezone, setTimezone] = useState(saved.timezone)
  const [csv, setCsv] = useState<string | null>(saved.csv_timezone)
  const [dayZone, setDayZone] = useState(saved.trading_day_timezone)
  const [dayStart, setDayStart] = useState(saved.trading_day_start)
  const [justSaved, setJustSaved] = useState(false)
  const zones = timeZones([saved.timezone, saved.trading_day_timezone, timezone, dayZone])

  const changes = {
    ...(timezone !== saved.timezone ? { timezone } : {}),
    ...(csv !== saved.csv_timezone ? { csv_timezone: csv ?? '' } : {}), // "" goes back to following the timezone
    ...(dayZone !== saved.trading_day_timezone ? { trading_day_timezone: dayZone } : {}),
    ...(dayStart !== saved.trading_day_start ? { trading_day_start: dayStart } : {}),
  }
  const dirty = Object.keys(changes).length > 0
  const movesTradingDay = 'trading_day_timezone' in changes || 'trading_day_start' in changes
  const startValid = isClockTime(dayStart)
  const problem = save.error instanceof ApiError ? save.error : null
  const fields = problem?.fields ?? {}

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!dirty || !startValid) return
    setJustSaved(false)
    save.mutate(changes, { onSuccess: () => setJustSaved(true) })
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Card className="flex flex-col gap-4 px-5 py-4" role="region" aria-label="Time zone">
        <h2 className="m-0 text-base font-semibold">Time zone</h2>
        <Field
          id="timezone"
          label="Your time zone"
          hint="The times in your Tradovate files are read in this zone, and trade times are shown in it."
          error={fields['timezone']}
        >
          <ZoneSelect
            id="timezone"
            value={timezone}
            onChange={(v) => {
              setTimezone(v)
              setJustSaved(false)
            }}
            zones={zones}
          />
        </Field>
      </Card>

      <Card className="flex flex-col gap-4 px-5 py-4" role="region" aria-label="Tradovate files">
        <h2 className="m-0 text-base font-semibold">Tradovate files</h2>
        <CsvZoneChoice
          ownZone={timezone}
          value={csv}
          onChange={(next) => {
            setCsv(next)
            setJustSaved(false)
          }}
        />
        {fields['csv_timezone'] ? (
          <p role="alert" className="m-0 text-xs text-neg">
            {fields['csv_timezone']}
          </p>
        ) : null}
        {'csv_timezone' in changes ? (
          <p role="note" className="m-0 rounded-lg border border-warn-line px-3 py-2 text-[13px] text-warn">
            Files you import from now on are read in this zone. Imports you have already made are not changed.
          </p>
        ) : null}
        <p className="m-0 text-xs text-muted">
          If this is wrong, trades land hours off and on the wrong trading day. You can fix it later.
        </p>
      </Card>

      <Card className="flex flex-col gap-4 px-5 py-4" role="region" aria-label="Trading day">
        <h2 className="m-0 text-base font-semibold">Trading day</h2>
        <p className="m-0 text-muted">
          Futures sessions run through the evening, so a trading day starts the evening before. The default, 18:00 New
          York time, is how the CME (the exchange) counts days.
        </p>
        <div className="flex flex-wrap gap-4">
          <div className="min-w-[220px] flex-[2_1_260px]">
            <Field
              id="day-zone"
              label="Trading day time zone"
              hint="The zone the start time below is in."
              error={fields['trading_day_timezone']}
            >
              <ZoneSelect
                id="day-zone"
                value={dayZone}
                onChange={(v) => {
                  setDayZone(v)
                  setJustSaved(false)
                }}
                zones={zones}
              />
            </Field>
          </div>
          <div className="min-w-[140px] flex-[1_1_140px]">
            <Field
              id="day-start"
              label="A trading day starts at"
              hint="The default is 6:00 PM (18:00)."
              error={fields['trading_day_start'] ?? (startValid ? undefined : 'Use a time like 18:00.')}
            >
              <input
                id="day-start"
                type="time"
                value={dayStart}
                onChange={(e) => {
                  setDayStart(e.target.value)
                  setJustSaved(false)
                }}
                className={CONTROL}
              />
            </Field>
          </div>
        </div>
        {movesTradingDay ? (
          <p role="note" className="m-0 rounded-lg border border-warn-line px-3 py-2 text-[13px] text-warn">
            Saving this moves trades to the day they belong to under the new settings, and your statistics are worked
            out again.
          </p>
        ) : null}
      </Card>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={!dirty || !startValid || save.isPending}>
          {save.isPending ? 'Saving…' : 'Save changes'}
        </Button>
        {justSaved && !dirty ? (
          <span role="status" className="text-[13px] text-pos">
            Saved
          </span>
        ) : null}
        {problem && Object.keys(fields).length === 0 ? (
          <span role="alert" className="text-[13px] text-neg">
            {problem.message}
          </span>
        ) : null}
        {save.isError && !problem ? (
          <span role="alert" className="text-[13px] text-neg">
            Could not save. Check your connection and try again.
          </span>
        ) : null}
      </div>
    </form>
  )
}

function ThemeCard({ theme }: { theme: Theme }) {
  const save = useSaveSettings()
  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Appearance">
      <h2 className="m-0 text-base font-semibold">Appearance</h2>
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          label="Theme"
          options={THEMES}
          value={theme}
          onChange={(next) => {
            applyTheme(next) // at once; the account copy follows
            save.mutate({ theme: next }, { onError: () => applyTheme(theme) })
          }}
        />
        <span className="text-xs text-muted">Applies on every device you sign in on.</span>
      </div>
    </Card>
  )
}

/** Time zones, the trading day and the theme: the only settings the API stores for a person today. */
export function PreferencesPage() {
  const session = useSession()
  if (session.isPending) return <Spinner label="Loading your settings" />
  if (!session.data) return null
  const settings = session.data.settings
  return (
    <>
      <SectionHeader title="Preferences">How times are read and shown, and how the app looks.</SectionHeader>
      <Form saved={settings} />
      <ThemeCard theme={settings.theme} />
    </>
  )
}
