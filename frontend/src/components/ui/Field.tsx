import type { ReactNode } from 'react'

/** The class of a text box, a select or a date box, so every form looks the same. */
export const CONTROL = 'h-10 w-full rounded-lg border border-line-strong bg-bg px-3 font-[inherit] text-ink'

/** A form field: its label, the control, a line of help, and the reason it was refused (read out when it appears). */
export function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string
  label: string
  hint?: string
  error?: string | undefined
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium">
        {label}
      </label>
      {children}
      {hint ? <p className="m-0 text-xs text-muted">{hint}</p> : null}
      {error ? (
        <p role="alert" className="m-0 text-xs text-neg">
          {error}
        </p>
      ) : null}
    </div>
  )
}
