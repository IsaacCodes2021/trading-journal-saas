import type { Editor } from '@tiptap/react'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Button, Card } from '../../components/ui/primitives'
import type { TradeDetail } from '../../lib/data/model'
import { useSaveNotes } from '../../lib/data/queries'

// The editor is the biggest piece of this page, so it loads after the rest of it has appeared.
const NotesEditor = lazy(() => import('./NotesEditor'))

const SAVE_AFTER_MS = 800

type Status = 'saved' | 'unsaved' | 'saving' | 'failed'

const STATUS_TEXT: Record<Status, string> = {
  saved: 'Saved',
  unsaved: 'Unsaved changes',
  saving: 'Saving…',
  failed: 'Could not save. Your text is still here.',
}

/**
 * The journal note for one trade. It saves by itself a moment after typing stops, and when the editor loses focus (so
 * moving to the next trade never drops a change). Keyed by trade in the page, so each trade starts with its own text.
 * `onEditor` hands the editor to a caller that wants to drive it (the tests do).
 */
export function NotesCard({ trade, onEditor }: { trade: TradeDetail; onEditor?: (editor: Editor) => void }) {
  const save = useSaveNotes(trade.id)
  const [status, setStatus] = useState<Status>('saved')
  const latest = useRef<string | null>(null) // what the editor holds now; null until it has started
  const saved = useRef<string | null>(null) // what the server holds, as the editor writes it
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const persist = () => {
    clearTimeout(timer.current)
    const value = latest.current
    if (value === null) return
    if (value === saved.current) {
      setStatus('saved')
      return
    }
    setStatus('saving')
    save.mutate(value, {
      onSuccess: () => {
        saved.current = value
        setStatus(latest.current === value ? 'saved' : 'unsaved')
      },
      onError: () => setStatus('failed'),
    })
  }

  const changed = (html: string) => {
    latest.current = html
    setStatus(html === saved.current ? 'saved' : 'unsaved')
    clearTimeout(timer.current)
    timer.current = setTimeout(persist, SAVE_AFTER_MS)
  }

  useEffect(() => () => clearTimeout(timer.current), [])

  return (
    <Card className="p-0" role="region" aria-label="Notes">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3.5">
        <h2 className="m-0 text-base font-semibold">Notes</h2>
        <span className="grow" />
        <span role="status" className="text-xs text-muted">
          {STATUS_TEXT[status]}
        </span>
        {status === 'failed' ? <Button onClick={persist}>Try again</Button> : null}
      </div>
      <Suspense
        fallback={
          <p role="status" className="m-0 min-h-[300px] px-5 py-4 text-muted">
            Loading the editor…
          </p>
        }
      >
        <NotesEditor
          html={trade.notes_html}
          onChange={changed}
          onBlur={persist}
          onReady={(start, editor) => {
            latest.current = start
            saved.current = start
            onEditor?.(editor)
          }}
        />
      </Suspense>
    </Card>
  )
}
