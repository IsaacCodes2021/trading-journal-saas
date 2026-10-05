import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Button, Card } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { ScreenshotRef, TradeDetail } from '../../lib/data/model'
import { useAddScreenshots, useDeleteScreenshot } from '../../lib/data/queries'
import { pluralize } from '../../lib/format'
import { MAX_IMAGE_MB } from '../../lib/limits'
import { Lightbox, Picture } from './Lightbox'

const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp'

const isImage = (file: File) => file.type.startsWith('image/')

/** "entry.png" to "entry": the label under a thumbnail. */
const labelOf = (shot: ScreenshotRef) => shot.original_name.replace(/\.[^.]+$/, '')

/**
 * Screenshots for one trade: drop, browse or paste (anywhere on the page) to add; click one to see it full size.
 * The server decides what is acceptable (it reads the file's bytes); a refusal is shown in words.
 */
export function ScreenshotsCard({ trade }: { trade: TradeDetail }) {
  const add = useAddScreenshots(trade.id)
  const remove = useDeleteScreenshot(trade.id)
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [viewing, setViewing] = useState<number | null>(null) // index into the trade's screenshots
  const [message, setMessage] = useState<{ kind: 'notice' | 'refused'; text: string } | null>(null)

  const take = (files: readonly File[]) => {
    const images = files.filter(isImage)
    if (images.length === 0) {
      if (files.length > 0) setMessage({ kind: 'refused', text: 'Only images can be added here.' })
      return
    }
    setMessage(null)
    add.mutate(images, {
      onSuccess: (added) => setMessage({ kind: 'notice', text: `Added ${pluralize(added.length, 'image')}.` }),
      onError: (error) =>
        setMessage({ kind: 'refused', text: error instanceof Error ? error.message : 'Could not add the image.' }),
    })
  }

  // Pasting a picture works wherever focus is on the page, as long as the clipboard holds an image.
  const takeRef = useRef(take)
  useEffect(() => {
    takeRef.current = take
  })
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const files = [...(event.clipboardData?.files ?? [])].filter(isImage)
      if (files.length === 0) return
      event.preventDefault()
      takeRef.current(files)
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
  }, [])

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    take([...event.dataTransfer.files])
  }

  const shots = trade.screenshots
  return (
    <Card className="px-5 py-4" role="region" aria-label="Screenshots">
      <div className="mb-2.5 flex items-center gap-2">
        <h2 className="m-0 text-[13px] font-semibold tracking-wide text-muted uppercase">Screenshots</h2>
        <span className="grow" />
        <span className="text-xs text-muted">PNG, JPEG, GIF or WebP, up to {MAX_IMAGE_MB} MB each</span>
      </div>

      {shots.length > 0 ? (
        <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-2.5 p-0">
          {shots.map((shot, i) => (
            <li key={shot.id}>
              <button
                type="button"
                aria-label={`View ${shot.original_name}`}
                onClick={() => setViewing(i)}
                className="relative block aspect-[16/10] w-full cursor-pointer overflow-hidden rounded-lg border border-line bg-bg p-0"
              >
                {shot.src ? (
                  <Picture shot={shot} className="size-full object-cover" />
                ) : (
                  <svg viewBox="0 0 160 100" width="100%" height="100%" preserveAspectRatio="none" aria-hidden="true">
                    <path
                      d="M0,70 L20,64 L40,68 L60,52 L80,56 L100,36 L120,40 L140,22 L160,18"
                      fill="none"
                      strokeWidth="2"
                      className="stroke-pos"
                    />
                  </svg>
                )}
                <span className="absolute bottom-1.5 left-2 text-[11px] text-muted">{labelOf(shot)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0 text-muted">No screenshots yet.</p>
      )}

      <div
        onDragOver={(e) => e.preventDefault()}
        onDragEnter={() => setDragging(true)}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cx(
          'mt-2.5 rounded-lg border border-dashed p-4 text-center text-muted',
          dragging ? 'border-accent bg-raised' : 'border-line-strong',
        )}
      >
        Drop images here, paste from the clipboard, or{' '}
        <Button variant="ghost" className="h-auto px-1 py-0 text-accent" onClick={() => input.current?.click()}>
          browse
        </Button>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          multiple
          tabIndex={-1}
          aria-label="Choose images to add"
          className="sr-only"
          onChange={(event) => {
            take([...(event.target.files ?? [])])
            event.target.value = '' // so choosing the same file again still counts
          }}
        />
      </div>

      {add.isPending ? (
        <p role="status" className="mt-2 mb-0 text-xs text-muted">
          Adding…
        </p>
      ) : null}
      {message ? (
        <p
          role={message.kind === 'refused' ? 'alert' : 'status'}
          className={cx('mt-2 mb-0 text-xs', message.kind === 'refused' ? 'text-neg' : 'text-muted')}
        >
          {message.text}
        </p>
      ) : null}

      {viewing !== null && shots[viewing] ? (
        <Lightbox
          shots={shots}
          index={viewing}
          onIndex={setViewing}
          onClose={() => setViewing(null)}
          deleting={remove.isPending}
          onDelete={(shot) =>
            remove.mutate(shot.id, {
              onSuccess: () => {
                setViewing(null)
                setMessage({ kind: 'notice', text: 'Image deleted.' })
              },
              onError: () => setMessage({ kind: 'refused', text: 'The image could not be deleted.' }),
            })
          }
        />
      ) : null}
    </Card>
  )
}
