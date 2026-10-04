import { useState } from 'react'
import { CONTROL } from '../../components/ui/Field'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { ACTION_NAMES, actionText, utcMoment } from '../../lib/admin'
import { metaText, useAudit, type AuditQuery } from '../../lib/adminCodes'
import { messageOf } from '../tags/messages'

const ACTORS = [
  { value: '', label: 'Anyone' },
  { value: 'user', label: 'The person' },
  { value: 'admin', label: 'An operator' },
  { value: 'system', label: 'The system' },
  { value: 'cli:owner', label: 'The owner’s command' },
]

const DAYS: { value: AuditQuery['days']; label: string }[] = [
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 0, label: 'All time' },
]

/** How long to wait after the last keystroke before searching. */
export const AUDIT_SEARCH_DELAY_MS = 350

function SearchBox({ value, onSearch }: { value: string; onSearch: (text: string) => void }) {
  const [text, setText] = useState(value)
  const [seen, setSeen] = useState(value)
  const [sent, setSent] = useState(value)
  const send = (next: string) => {
    setSent(next)
    onSearch(next)
  }
  if (value !== seen) {
    setSeen(value)
    if (value !== sent) setText(value)
  }
  const [timer, setTimer] = useState<ReturnType<typeof setTimeout> | null>(null)
  const change = (next: string) => {
    setText(next)
    if (timer) clearTimeout(timer)
    setTimer(next.trim() === sent ? null : setTimeout(() => send(next.trim()), AUDIT_SEARCH_DELAY_MS))
  }
  return (
    <input
      type="search"
      aria-label="Search the log by email or id"
      placeholder="Search by email or id"
      value={text}
      maxLength={100}
      onChange={(e) => change(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          if (timer) clearTimeout(timer)
          send(text.trim())
        }
      }}
      className={`${CONTROL} h-9! w-[220px]! max-w-full`}
    />
  )
}

/** The audit log, newest first, with what narrows it, and a button for the older events. */
export function AuditLog({ query, onQuery }: { query: AuditQuery; onQuery: (next: AuditQuery) => void }) {
  const log = useAudit(query)
  const events = log.data?.pages.flatMap((page) => page.events) ?? []
  return (
    <Card className="p-0" role="region" aria-label="Audit log">
      <div className="flex flex-wrap items-center gap-2.5 border-b border-line px-4 py-3.5">
        <h2 className="m-0 text-base font-semibold">Audit log</h2>
        <span className="grow" />
        <SearchBox value={query.q} onSearch={(q) => onQuery({ ...query, q })} />
        <select
          aria-label="Filter by what happened"
          value={query.action}
          onChange={(e) => onQuery({ ...query, action: e.target.value })}
          className={`${CONTROL} h-9! w-auto!`}
        >
          <option value="">Anything</option>
          {ACTION_NAMES.map((name) => (
            <option key={name} value={name}>
              {actionText(name)}
            </option>
          ))}
          {query.action && !ACTION_NAMES.includes(query.action) ? (
            <option value={query.action}>{query.action}</option>
          ) : null}
        </select>
        <select
          aria-label="Filter by who did it"
          value={query.actor}
          onChange={(e) => onQuery({ ...query, actor: e.target.value })}
          className={`${CONTROL} h-9! w-auto!`}
        >
          {ACTORS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
          {query.actor && !ACTORS.some((a) => a.value === query.actor) ? (
            <option value={query.actor}>{query.actor}</option>
          ) : null}
        </select>
        <select
          aria-label="How far back"
          value={query.days}
          onChange={(e) => onQuery({ ...query, days: Number(e.target.value) as AuditQuery['days'] })}
          className={`${CONTROL} h-9! w-auto!`}
        >
          {DAYS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
      </div>
      {log.isPending ? <Spinner label="Loading the log" /> : null}
      {log.isError ? (
        <p role="alert" className="m-0 px-4 py-4 text-neg">
          {messageOf(log.error)}
        </p>
      ) : null}
      {log.data && events.length === 0 ? <p className="m-0 px-4 py-4 text-muted">Nothing matches.</p> : null}
      {events.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="border-b border-line px-3.5 py-2.5 font-medium">When</th>
                <th className="border-b border-line px-3.5 py-2.5 font-medium">Who</th>
                <th className="border-b border-line px-3.5 py-2.5 font-medium">What</th>
                <th className="border-b border-line px-3.5 py-2.5 font-medium">About</th>
                <th className="border-b border-line px-3.5 py-2.5 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td className="num border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap">
                    {utcMoment(event.at)}
                  </td>
                  <td className="border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap">
                    {event.actor_email ?? (event.actor === 'user' ? 'The person' : event.actor)}
                  </td>
                  <td className="border-b border-line-soft px-3.5 py-2.5">{actionText(event.action)}</td>
                  <td className="border-b border-line-soft px-3.5 py-2.5 whitespace-nowrap">
                    {event.user_email ?? (event.user_id ? 'An account' : '—')}
                  </td>
                  <td className="border-b border-line-soft px-3.5 py-2.5 text-muted">{metaText(event.meta)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {log.hasNextPage ? (
        <div className="border-t border-line px-4 py-2.5">
          <Button onClick={() => void log.fetchNextPage()} disabled={log.isFetchingNextPage}>
            {log.isFetchingNextPage ? 'Loading…' : 'Older events'}
          </Button>
        </div>
      ) : null}
    </Card>
  )
}
