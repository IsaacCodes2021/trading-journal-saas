import { useEffect, useRef, useState } from 'react'
import { Button } from '../../components/ui/primitives'
import type { ScreenshotRef } from '../../lib/data/model'

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])'

export function Picture({ shot, className }: { shot: ScreenshotRef; className?: string }) {
  if (shot.src) return <img src={shot.src} alt={shot.original_name} className={className} />
  return (
    <div
      role="img"
      aria-label={`${shot.original_name} (no preview in the sample data)`}
      className={`flex items-center justify-center bg-bg p-6 text-center text-muted ${className ?? ''}`}
    >
      {shot.original_name}
      <br />
      No preview in the sample data
    </div>
  )
}

/**
 * A screenshot at full size, as a modal dialog: Escape closes it, the arrow keys step through the trade's images, Tab
 * stays inside, and focus returns to the thumbnail that opened it. Deleting asks first.
 */
export function Lightbox({
  shots,
  index,
  onIndex,
  onClose,
  onDelete,
  deleting,
}: {
  shots: readonly ScreenshotRef[]
  index: number
  onIndex: (next: number) => void
  onClose: () => void
  onDelete: (shot: ScreenshotRef) => void
  deleting: boolean
}) {
  const shot = shots[index]
  const panel = useRef<HTMLDivElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const [confirmingId, setConfirmingId] = useState<number | null>(null) // asking about this image, if any

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    closeButton.current?.focus()
    return () => opener?.focus()
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      else if (event.key === 'ArrowLeft' && index > 0) onIndex(index - 1)
      else if (event.key === 'ArrowRight' && index < shots.length - 1) onIndex(index + 1)
      else if (event.key === 'Tab' && panel.current) {
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
  }, [index, shots.length, onClose, onIndex])

  if (!shot) return null
  const confirming = confirmingId === shot.id
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close the image"
        onClick={onClose}
        className="absolute inset-0 cursor-default border-0 bg-black/70"
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={`Screenshot ${index + 1} of ${shots.length}`}
        className="relative flex max-h-full w-full max-w-[960px] flex-col gap-3 rounded-[10px] border border-line bg-panel p-4"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-semibold">{shot.original_name}</span>
          <span className="text-xs text-muted">
            {index + 1} of {shots.length}
          </span>
          <span className="grow" />
          <Button aria-label="Previous image" disabled={index === 0} onClick={() => onIndex(index - 1)}>
            &larr;
          </Button>
          <Button aria-label="Next image" disabled={index >= shots.length - 1} onClick={() => onIndex(index + 1)}>
            &rarr;
          </Button>
          <Button ref={closeButton} onClick={onClose}>
            Close
          </Button>
        </div>
        <div className="flex min-h-0 justify-center overflow-auto">
          <Picture shot={shot} className="max-h-[70vh] max-w-full rounded-lg object-contain" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {confirming ? (
            <>
              <span role="alert" className="text-[13px]">
                Delete this image? This cannot be undone.
              </span>
              <Button disabled={deleting} onClick={() => onDelete(shot)} className="border-neg text-neg">
                Yes, delete it
              </Button>
              <Button onClick={() => setConfirmingId(null)}>Keep it</Button>
            </>
          ) : (
            <Button onClick={() => setConfirmingId(shot.id)}>Delete image</Button>
          )}
        </div>
      </div>
    </div>
  )
}
