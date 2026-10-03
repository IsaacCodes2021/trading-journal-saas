import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { cx } from './cx'

/**
 * A button that opens a small panel (a filter's choices, the account menu). Closes on Escape, on a click outside, and
 * when focus leaves. The button says whether the panel is open; the panel is a labelled group, so a screen reader
 * announces what it is.
 */
export function Popover({
  label,
  button,
  children,
  align = 'left',
  buttonClassName,
}: {
  /** Names the panel for assistive technology, e.g. "Firms". */
  label: string
  button: ReactNode
  children: (close: () => void) => ReactNode
  align?: 'left' | 'right'
  buttonClassName?: string
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div
      ref={root}
      className="relative"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false)
      }}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-haspopup="true"
        onClick={() => setOpen((value) => !value)}
        className={cx(
          'inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-raised px-3 font-[inherit] text-ink hover:border-line-strong',
          buttonClassName,
        )}
      >
        {button}
      </button>
      {open ? (
        <div
          id={panelId}
          role="group"
          aria-label={label}
          className={cx(
            'absolute z-20 mt-1.5 min-w-[200px] rounded-[10px] border border-line bg-panel p-2 shadow-lg',
            align === 'right' ? 'right-0' : 'left-0',
          )}
        >
          {children(() => setOpen(false))}
        </div>
      ) : null}
    </div>
  )
}

export function Chevron() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}
