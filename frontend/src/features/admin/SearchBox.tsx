import { useEffect, useState } from 'react'
import { CONTROL } from '../../components/ui/Field'

/** How long to wait after the last keystroke before searching. */
export const SEARCH_DELAY_MS = 350

/**
 * A search box that asks after a short pause, or at once on Enter. `value` is the address's search: when it changes by
 * itself (Back, a shared link) the box follows it.
 */
export function SearchBox({
  value,
  onSearch,
  label,
  placeholder,
}: {
  value: string
  onSearch: (text: string) => void
  label: string
  placeholder: string
}) {
  const [text, setText] = useState(value)
  const [seen, setSeen] = useState(value) // the address's search as of the last render
  const [sent, setSent] = useState(value) // what this box last asked for itself
  const send = (next: string) => {
    setSent(next)
    onSearch(next)
  }
  // The address changed by itself: follow it. A change this box asked for is not followed, so keystrokes made while the
  // page was answering are not overwritten.
  if (value !== seen) {
    setSeen(value)
    if (value !== sent) setText(value)
  }

  useEffect(() => {
    if (text.trim() === sent) return
    const timer = setTimeout(() => send(text.trim()), SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
    // `send` only sets state and calls the page's handler; it changes every render and must not restart the wait.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, sent])

  return (
    <input
      type="search"
      aria-label={label}
      placeholder={placeholder}
      value={text}
      maxLength={100}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') send(text.trim())
      }}
      className={`${CONTROL} h-9! w-[260px]! max-w-full`}
    />
  )
}
