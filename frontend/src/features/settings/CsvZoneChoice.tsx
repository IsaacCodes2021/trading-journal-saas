import { useId, useState } from 'react'
import { CONTROL } from '../../components/ui/Field'
import { cx } from '../../components/ui/cx'
import { timeZones, zoneLabel } from '../../lib/timezones'

/** The CME's own clock: what an export carries when it is stamped in exchange time. */
export const EXCHANGE_ZONE = 'America/Chicago'

type Mode = 'same' | 'exchange' | 'other'

const modeOf = (value: string | null): Mode =>
  value === null ? 'same' : value === EXCHANGE_ZONE ? 'exchange' : 'other'

/**
 * Which clock the times in a person's Tradovate files use. Getting it wrong lands trades hours off and on the wrong
 * trading day, so it is asked plainly, with the three answers a trader gives: the same as their own timezone, exchange
 * time, or something else from the full list. `value` is the chosen zone, or null for "the same as my timezone".
 */
export function CsvZoneChoice({
  ownZone,
  value,
  onChange,
}: {
  ownZone: string
  value: string | null
  onChange: (next: string | null) => void
}) {
  const name = useId()
  const [mode, setMode] = useState<Mode>(modeOf(value))
  const [other, setOther] = useState(modeOf(value) === 'other' && value ? value : ownZone)

  const choose = (next: Mode) => {
    setMode(next)
    onChange(next === 'same' ? null : next === 'exchange' ? EXCHANGE_ZONE : other)
  }

  const options: { mode: Mode; title: string; detail: string }[] = [
    { mode: 'same', title: 'Same as my timezone', detail: zoneLabel(ownZone) },
    { mode: 'exchange', title: 'Exchange time', detail: `${zoneLabel(EXCHANGE_ZONE)} (CME)` },
    { mode: 'other', title: 'Something else', detail: 'Pick from the full list' },
  ]

  return (
    <div role="radiogroup" aria-label="Timezone of Tradovate CSV exports" className="flex flex-col gap-2">
      <div className="text-[13px] font-medium">Which timezone are your Tradovate CSV exports in?</div>
      {options.map((option) => {
        const id = `${name}-${option.mode}`
        const { title, detail } = option
        return (
          <label
            key={option.mode}
            htmlFor={id}
            className={cx(
              'flex cursor-pointer items-start gap-3 rounded-lg border px-3.5 py-3',
              mode === option.mode ? 'border-chip-selected-line bg-chip-selected-bg' : 'border-line bg-bg',
            )}
          >
            <input
              id={id}
              type="radio"
              name={name}
              checked={mode === option.mode}
              onChange={() => choose(option.mode)}
              className="mt-1"
            />
            <span className="flex flex-col font-medium">
              {title}
              <span className="text-[13px] font-normal text-muted">{detail}</span>
            </span>
          </label>
        )
      })}
      {mode === 'other' ? (
        <select
          aria-label="Timezone of your CSV exports"
          value={other}
          onChange={(e) => {
            setOther(e.target.value)
            onChange(e.target.value)
          }}
          className={CONTROL}
        >
          {timeZones([other, ownZone]).map((zone) => (
            <option key={zone} value={zone}>
              {zoneLabel(zone)}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  )
}
