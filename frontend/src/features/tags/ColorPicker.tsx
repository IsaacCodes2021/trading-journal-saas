import { useId } from 'react'
import { CONTROL } from '../../components/ui/Field'
import { cx } from '../../components/ui/cx'
import type { ColorPreset } from '../../lib/data/model'

function Swatch({
  color,
  label,
  chosen,
  onPick,
}: {
  color: string
  label: string
  chosen: boolean
  onPick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={chosen}
      onClick={onPick}
      className={cx(
        'size-7 cursor-pointer rounded-full border-2 p-0',
        chosen ? 'border-ink' : 'border-transparent hover:border-line-strong',
      )}
      style={{ background: color }}
    />
  )
}

/** Choose a tag's colour from the presets, from colours already used on other tags, or by typing a code like #2a78d6. */
export function ColorPicker({
  value,
  onChange,
  presets,
  custom,
}: {
  value: string
  onChange: (color: string) => void
  presets: ColorPreset[]
  custom: string[]
}) {
  const id = useId()
  const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase()
  return (
    <div className="flex flex-col gap-1.5" role="group" aria-label="Colour">
      <span className="text-[13px] font-medium">Colour</span>
      <div className="flex flex-wrap gap-1.5">
        {presets.map((preset) => (
          <Swatch
            key={preset.color}
            color={preset.color}
            label={preset.name}
            chosen={same(value, preset.color)}
            onPick={() => onChange(preset.color)}
          />
        ))}
        {custom.map((color) => (
          <Swatch
            key={color}
            color={color}
            label={`Used colour ${color}`}
            chosen={same(value, color)}
            onPick={() => onChange(color)}
          />
        ))}
      </div>
      <label htmlFor={id} className="text-xs text-muted">
        Or type a colour code, like #2a78d6
      </label>
      <input
        id={id}
        className={cx(CONTROL, 'max-w-[160px] font-mono')}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
        spellCheck={false}
      />
    </div>
  )
}
