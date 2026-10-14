import { CONTROL } from './ui/Field'
import { cx } from './ui/cx'
import type { RuleField, RuleFlag } from '../lib/data/model'
import { warningOf } from '../lib/rules'

const TEXTAREA = 'w-full rounded-lg border border-line-strong bg-bg px-3 py-2 font-[inherit] text-ink'

/** One rule field. Red when the research could not confirm it (with why, as a tooltip and in words) or when it is wrong. */
export function RuleInput({
  field,
  value,
  warning,
  error,
  onChange,
}: {
  field: RuleField
  value: string
  warning: string | null
  error: string | undefined
  onChange: (next: string) => void
}) {
  const id = `rule-${field.name}`
  const red = warning !== null || error !== undefined
  const described = [warning ? `${id}-warning` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ') || undefined
  const common = {
    id,
    value,
    title: warning ?? undefined,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': described,
  }
  const redClass = red ? 'border-neg bg-flag-bg' : ''
  let control
  if (field.kind === 'choice') {
    control = (
      <select {...common} onChange={(e) => onChange(e.target.value)} className={cx(CONTROL, redClass)}>
        <option value="">—</option>
        {field.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    )
  } else if (field.kind === 'text' && field.group !== 'Rule set') {
    control = (
      <textarea {...common} rows={2} onChange={(e) => onChange(e.target.value)} className={cx(TEXTAREA, redClass)} />
    )
  } else {
    control = (
      <input
        {...common}
        type={field.kind === 'date' ? 'date' : 'text'}
        inputMode={
          field.kind === 'money' || field.kind === 'pct' ? 'decimal' : field.kind === 'int' ? 'numeric' : undefined
        }
        onChange={(e) => onChange(e.target.value)}
        className={cx(CONTROL, redClass, field.kind !== 'text' && field.kind !== 'date' && 'num')}
      />
    )
  }
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium">
        {field.label}
        {field.kind === 'money' ? ' ($)' : field.kind === 'pct' ? ' (%)' : ''}
      </label>
      {control}
      {field.hint ? <p className="m-0 text-xs text-muted">{field.hint}</p> : null}
      {warning ? (
        <p id={`${id}-warning`} className="m-0 text-xs text-neg">
          {warning}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} role="alert" className="m-0 text-xs text-neg">
          {error}
        </p>
      ) : null}
    </div>
  )
}

/** Every rule field, in the form's groups: the person's rules form and the operator's template form are the same. */
export function RuleGroups({
  fields,
  groups,
  entered,
  flags,
  notes,
  errors,
  onChange,
}: {
  fields: readonly RuleField[]
  groups: readonly string[]
  entered: Record<string, string>
  flags: readonly RuleFlag[]
  notes: Record<string, string>
  errors: Record<string, string>
  onChange: (name: string, value: string) => void
}) {
  return groups.map((group) => {
    const inGroup = fields.filter((f) => f.group === group)
    if (inGroup.length === 0) return null
    return (
      <fieldset key={group} className="m-0 flex flex-col gap-3 border-0 p-0">
        <legend className="mb-2 p-0 text-sm font-semibold">{group}</legend>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {inGroup.map((field) => (
            <RuleInput
              key={field.name}
              field={field}
              value={entered[field.name] ?? ''}
              warning={warningOf(field.name, flags, notes)}
              error={errors[field.name]}
              onChange={(next) => onChange(field.name, next)}
            />
          ))}
        </div>
      </fieldset>
    )
  })
}
