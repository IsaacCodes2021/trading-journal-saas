import { useEffect, useRef, useState } from 'react'
import { Button, Card } from '../../components/ui/primitives'
import type { TradeDetail } from '../../lib/data/model'
import { useSaveNotes } from '../../lib/data/queries'
import { htmlToText, textToHtml } from './notes'

const SAVE_AFTER_MS = 800

type Status = 'saved' | 'unsaved' | 'saving' | 'failed'

const STATUS_TEXT: Record<Status, string> = {
  saved: 'Saved',
  unsaved: 'Unsaved changes',
  saving: 'Saving…',
  failed: 'Could not save. Your text is still here.',
}

/**
 * The journal note for one trade. It saves by itself a moment after typing stops, and when the box loses focus (so
 * moving to the next trade never drops a change). The editor here is plain text until the rich-text editor is
 * installed; see notes.ts. Keyed by trade in the page, so each trade starts with its own text.
 */
export function NotesCard({ trade }: { trade: TradeDetail }) {
  const save = useSaveNotes(trade.id)
  const [text, setText] = useState(() => htmlToText(trade.notes_html))
  const [status, setStatus] = useState<Status>('saved')
  const latest = useRef(text)
  const saved = useRef(text)

  const persist = (value: string) => {
    if (value === saved.current) {
      setStatus('saved')
      return
    }
    setStatus('saving')
    save.mutate(textToHtml(value), {
      onSuccess: () => {
        saved.current = value
        setStatus(latest.current === value ? 'saved' : 'unsaved')
      },
      onError: () => setStatus('failed'),
    })
  }

  useEffect(() => {
    if (text === saved.current) return
    const timer = setTimeout(() => persist(text), SAVE_AFTER_MS)
    return () => clearTimeout(timer)
    // `persist` only reads refs and stable mutation callbacks; the text is what restarts the timer
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])

  return (
    <Card className="p-0" role="region" aria-label="Notes">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3.5">
        <h2 className="m-0 text-base font-semibold">Notes</h2>
        <span className="grow" />
        <span role="status" className="text-xs text-muted">
          {STATUS_TEXT[status]}
        </span>
        {status === 'failed' ? <Button onClick={() => persist(text)}>Try again</Button> : null}
      </div>
      <div className="px-5 py-4">
        <textarea
          aria-label="Notes"
          value={text}
          rows={10}
          placeholder="What was the plan, what happened, what would you change?"
          onChange={(event) => {
            latest.current = event.target.value
            setText(event.target.value)
            setStatus(event.target.value === saved.current ? 'saved' : 'unsaved')
          }}
          onBlur={() => persist(text)}
          className="min-h-[260px] w-full resize-y rounded-lg border border-line-strong bg-bg p-3 font-[inherit] text-[15px] leading-relaxed text-ink"
        />
        <p className="mt-2 mb-0 text-xs text-muted">
          Plain text for now. Bold, lists and links arrive with the rich-text editor.
        </p>
      </div>
    </Card>
  )
}
