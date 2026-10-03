import { useEffect, useState } from 'react'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/primitives'
import type { ScreenshotRef } from '../../lib/data/model'

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
 * A screenshot at full size, as a modal dialog: the arrow keys step through the trade's images, and deleting asks
 * first. (Escape, Tab and focus are the modal's.)
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
  const [confirmingId, setConfirmingId] = useState<number | null>(null) // asking about this image, if any

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft' && index > 0) onIndex(index - 1)
      else if (event.key === 'ArrowRight' && index < shots.length - 1) onIndex(index + 1)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [index, shots.length, onIndex])

  if (!shot) return null
  const confirming = confirmingId === shot.id
  return (
    <Modal label={`Screenshot ${index + 1} of ${shots.length}`} onClose={onClose} className="max-w-[960px]">
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
        <Button data-autofocus onClick={onClose}>
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
    </Modal>
  )
}
