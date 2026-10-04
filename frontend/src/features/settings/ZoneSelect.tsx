import { CONTROL } from '../../components/ui/Field'
import { zoneLabel } from '../../lib/timezones'

/** A drop-down of time zones, labelled the way the rest of the app labels them. */
export function ZoneSelect({
  id,
  value,
  onChange,
  zones,
}: {
  id: string
  value: string
  onChange: (next: string) => void
  zones: string[]
}) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={CONTROL}>
      {zones.map((zone) => (
        <option key={zone} value={zone}>
          {zoneLabel(zone)}
        </option>
      ))}
    </select>
  )
}
