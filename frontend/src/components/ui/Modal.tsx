import { useEffect, useRef, type ReactNode } from 'react'
import { cx } from './cx'

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])'

/**
 * A modal dialog over the page. Escape and a click on the backdrop close it, Tab stays inside, focus goes to the
 * element marked `data-autofocus` (else the first control) and comes back to where it was when the dialog closes.
 * The caller decides what is in it, and whether closing is allowed right now (`onClose` can simply do nothing).
 */
export function Modal({
  label,
  onClose,
  children,
  className,
}: {
  label: string
  onClose: () => void
  children: ReactNode
  className?: string
}) {
  const panel = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const start =
      panel.current?.querySelector<HTMLElement>('[data-autofocus]') ??
      panel.current?.querySelector<HTMLElement>(FOCUSABLE)
    ;(start ?? panel.current)?.focus()
    return () => opener?.focus()
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        close.current()
      } else if (event.key === 'Tab' && panel.current) {
        const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
        const first = items[0]
        const last = items[items.length - 1]
        if (!first || !last) return
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close"
        onClick={() => close.current()}
        className="absolute inset-0 cursor-default border-0 bg-black/70"
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className={cx(
          'relative flex max-h-full w-full flex-col gap-3 overflow-auto rounded-[10px] border border-line bg-panel p-4',
          className,
        )}
      >
        {children}
      </div>
    </div>
  )
}
