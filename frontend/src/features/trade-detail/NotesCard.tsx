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

/** What saves a note: a react-query mutation (`mutate`) or anything that calls back when the save worked or did not. */
export interface NoteSaver {
  mutate: (html: string, callbacks: { onSuccess: () => void; onError: () => void }) => void
}

/** The journal note for one trade (see `NoteEditorCard`). Keyed by trade in the page, so each starts with its own text. */
export function NotesCard({ trade, onEditor }: { trade: TradeDetail; onEditor?: (editor: Editor) => void }) {
  const save = useSaveNotes(trade.id)
  return <NoteEditorCard title="Notes" html={trade.notes_html} saver={save} onEditor={onEditor} />
}

/**
 * A rich-text note that saves by itself a moment after typing stops, and when the editor loses focus (so moving on never
 * drops a change). Used for a trade's notes and a day's note. Keyed by what it is a note for, so each starts with its own
 * text. `onEditor` hands the editor to a caller that wants to drive it (the tests do).
 */
export function NoteEditorCard({
  title,
  html,
  saver: save,
  onEditor,
}: {
  title: string
  html: string
  saver: NoteSaver
  onEditor?: (editor: Editor) => void
}) {
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
    <Card className="p-0" role="region" aria-label={title}>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3.5">
        <h2 className="m-0 text-base font-semibold">{title}</h2>
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
          html={html}
          label={title}
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
