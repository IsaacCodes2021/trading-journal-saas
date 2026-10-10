import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import { utcDay } from '../../lib/admin'
import {
  BETA_PAGE,
  BETA_STATES,
  CODE_TEXT,
  STATE_TEXT,
  accessText,
  daysLeft,
  revokeKind,
  useBeta,
  useRevokeBeta,
  type BetaPerson,
  type BetaQuery,
  type BetaRevoked,
  type BetaState,
} from '../../lib/adminBeta'
import { messageOf } from '../tags/messages'
import { SearchBox } from './SearchBox'

const STATE_CLASS: Record<BetaState, string> = {
  waiting: 'bg-raised text-warn',
  active: 'bg-long-bg text-pos',
  ended: 'bg-raised text-muted',
  revoked: 'bg-short-bg text-neg',
}

const TH = 'border-b border-line px-3.5 py-2.5 font-medium'
const TD = 'border-b border-line-soft px-3.5 py-2.5'

const NOTICE: Record<BetaRevoked['notice'], string> = {
  sent: 'They were emailed that they need to sign up to continue.',
  pending: 'The email did not go out. Their access has ended all the same.',
  not_needed: 'They had no account, so only their code was withdrawn. Nothing was emailed.',
}

/** Where a person stands, as a small badge: "Active · 23 days left". */
function StateBadge({ person, now }: { person: BetaPerson; now: number }) {
  return (
    <span
      className={`inline-flex h-[22px] items-center rounded-md px-2 text-xs font-medium whitespace-nowrap ${STATE_CLASS[person.state]}`}
    >
      {accessText(person, now)}
    </span>
  )
}

function Filter({
  state,
  counts,
  onState,
}: {
  state: BetaState | ''
  counts: Record<string, number> | undefined
  onState: (state: BetaState | '') => void
}) {
  const items: { value: BetaState | ''; label: string; count: number | undefined }[] = [
    { value: '', label: 'All', count: counts?.['all'] },
    ...BETA_STATES.map((value) => ({ value, label: STATE_TEXT[value], count: counts?.[value] })),
  ]
  return (
    <div role="group" aria-label="Filter by state" className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          aria-pressed={state === item.value}
          onClick={() => onState(item.value)}
          className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full border border-line bg-transparent px-3 font-[inherit] text-[13px] text-muted hover:text-ink aria-pressed:border-line-strong aria-pressed:bg-raised aria-pressed:font-medium aria-pressed:text-ink"
        >
          {item.label}
          {/* A space between the words for a screen reader; in a flex row a blank text run is not drawn. */}
          {item.count === undefined ? null : ' '}
          {item.count === undefined ? null : <span className="num">{item.count.toLocaleString('en-US')}</span>}
        </button>
      ))}
    </div>
  )
}

/** Ask why, then end the access (or withdraw the code, for someone with no account). The reason is never emailed. */
function RevokeDialog({
  person,
  kind,
  onClose,
  onDone,
}: {
  person: BetaPerson
  kind: 'access' | 'code'
  onClose: () => void
  onDone: (result: BetaRevoked) => void
}) {
  const revoke = useRevokeBeta()
  const [reason, setReason] = useState('')
  const typed = reason.trim()
  const title = kind === 'access' ? 'Revoke beta access?' : 'Withdraw this code?'

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!typed || revoke.isPending) return
    revoke.mutate({ id: person.id, reason: typed }, { onSuccess: onDone })
  }

  return (
    <Modal label={title} onClose={revoke.isPending ? () => undefined : onClose} className="max-w-[520px]">
      <form onSubmit={submit} className="flex flex-col gap-3.5">
        <h2 className="m-0 text-lg font-semibold">{title}</h2>
        {kind === 'access' ? (
          <p className="m-0 text-muted">
            <strong className="text-ink">{person.first_name}</strong> ({person.email}) will lose access to Roundturn
            now. They will be emailed that they need to sign up to continue. Their journal is kept, read-only.
          </p>
        ) : (
          <p className="m-0 text-muted">
            <strong className="text-ink">{person.first_name}</strong> ({person.email}) has not made an account yet.
            Their code stops working now. Nothing is emailed.
          </p>
        )}
        <Field id="beta-reason" label="Reason (kept in the audit log, never emailed)">
          <textarea
            id="beta-reason"
            rows={3}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-autofocus
            className={`${CONTROL} h-auto py-2`}
          />
        </Field>
        {revoke.isError ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {messageOf(revoke.error)}
          </p>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button onClick={onClose} disabled={revoke.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            className="border-neg bg-neg! font-semibold text-accent-on! hover:opacity-90 disabled:hover:opacity-50"
            disabled={!typed || revoke.isPending}
          >
            {revoke.isPending ? 'Working…' : kind === 'access' ? 'Revoke access and send email' : 'Withdraw code'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="m-0">{children}</dd>
    </>
  )
}

/** One person's facts, and the one thing an operator can do to them. */
function Panel({ person, now, onRevoke }: { person: BetaPerson; now: number; onRevoke: () => void }) {
  const kind = revokeKind(person)
  const ends = person.access_ends_at
  return (
    <Card className="flex flex-col px-5 py-4" role="region" aria-label="Selected person">
      <h2 className="m-0 text-lg font-semibold">{person.first_name}</h2>
      <div className="text-muted">{person.email}</div>
      <dl className="mt-4 mb-0 grid grid-cols-[130px_minmax(0,1fr)] gap-x-3 gap-y-2.5 text-[13px]">
        <Row label="Favourite firm">{person.prop_firm || <span className="text-muted">None given</span>}</Row>
        <Row label="Joined">
          <span className="num">{utcDay(person.joined_at)}</span>
        </Row>
        <Row label="Sign-up code">{CODE_TEXT[person.code]}</Row>
        <Row label="Code email">
          {person.code_email_sent_at
            ? `Sent ${utcDay(person.code_email_sent_at)}`
            : person.code_email_failures > 0
              ? `Not sent (${person.code_email_failures} ${person.code_email_failures === 1 ? 'try' : 'tries'} failed)`
              : 'Not sent yet'}
        </Row>
        {ends ? (
          <Row label={person.state === 'active' ? 'Access ends' : 'Access ended'}>
            <span className="num">{utcDay(person.revoked_at ?? ends)}</span>
            {person.state === 'active' ? <span className="text-muted"> · {daysLeft(ends, now)} days</span> : null}
          </Row>
        ) : null}
        {person.state === 'active' || person.state === 'ended' || person.state === 'revoked' ? (
          <Row label="Ended email">
            {person.notice_sent_at ? `Sent ${utcDay(person.notice_sent_at)}` : 'Not sent yet'}
          </Row>
        ) : null}
      </dl>
      <div className="mt-5 flex flex-col gap-2.5 border-t border-line pt-4">
        {kind ? (
          <Button onClick={onRevoke} className="h-10 border-neg/50 text-neg hover:border-neg">
            {kind === 'access' ? 'Revoke beta access' : 'Withdraw code'}
          </Button>
        ) : (
          <p className="m-0 text-[13px] text-muted">There is nothing left to end for this person.</p>
        )}
      </div>
      <p className="mt-3.5 mb-0 text-xs text-muted">
        Revoking ends their access now and emails them that they need to sign up to continue. The same email goes out
        when the 30 days end.
      </p>
    </Card>
  )
}

/** The people who joined the beta, where each stands, and ending someone's access. State is in the address. */
export function BetaPage({
  query,
  selected,
  onQuery,
  onSelect,
  now: given,
}: {
  query: BetaQuery
  selected: number | null
  onQuery: (next: BetaQuery) => void
  onSelect: (id: number | null) => void
  /** The time the days left are counted from (a test passes its own). */
  now?: number
}) {
  // Read once, so the days left do not shift on a re-render (and a test can say what time it is).
  const [now] = useState(() => given ?? Date.now())
  const beta = useBeta(query)
  const total = beta.data?.total ?? 0
  const first = total === 0 ? 0 : (query.page - 1) * BETA_PAGE + 1
  const last = Math.min(query.page * BETA_PAGE, total)
  const people = beta.data?.people ?? []
  const open = people.find((p) => p.id === selected) ?? null
  const [revoking, setRevoking] = useState<BetaPerson | null>(null)
  const [result, setResult] = useState<{ name: string; notice: BetaRevoked['notice'] } | null>(null)
  const revokeOf = revoking ? revokeKind(revoking) : null

  return (
    <>
      <div role="note" className="rounded-lg border border-line-strong bg-chip-selected-bg px-3.5 py-2.5 text-[13px]">
        This list shows the name, address and favourite firm each person gave, and where their 30 days stand. Trades,
        notes and screenshots are never shown.
      </div>
      {result ? (
        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-panel px-3.5 py-2.5 text-[13px]"
        >
          <span>
            <strong>{result.name}</strong>: {NOTICE[result.notice]}
          </span>
          <Button variant="ghost" onClick={() => setResult(null)}>
            Dismiss
          </Button>
        </div>
      ) : null}
      <div className="flex flex-wrap items-start gap-4">
        <Card className="min-w-0 flex-[3_1_640px] p-0" role="region" aria-label="Beta testers">
          <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3.5">
            <SearchBox
              value={query.q}
              onSearch={(q) => onQuery({ ...query, q, page: 1 })}
              label="Search by name, email or firm"
              placeholder="Search by name, email or firm"
            />
            <Filter
              state={query.state}
              counts={beta.data?.counts}
              onState={(state) => onQuery({ ...query, state, page: 1 })}
            />
          </div>
          {beta.isPending ? <Spinner label="Loading beta testers" /> : null}
          {beta.isError ? (
            <p role="alert" className="m-0 px-4 py-4 text-neg">
              {messageOf(beta.error)}
            </p>
          ) : null}
          {beta.data ? (
            people.length === 0 ? (
              <p className="m-0 px-4 py-4 text-muted">
                {query.q || query.state ? 'Nobody matches.' : 'Nobody has joined the beta yet.'}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <caption className="sr-only">People who joined the beta</caption>
                  <thead>
                    <tr className="text-left text-xs text-muted">
                      <th scope="col" className={TH}>
                        Name
                      </th>
                      <th scope="col" className={TH}>
                        Email
                      </th>
                      <th scope="col" className={TH}>
                        Firm
                      </th>
                      <th scope="col" className={TH}>
                        Joined
                      </th>
                      <th scope="col" className={TH}>
                        Access
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {people.map((person) => (
                      <tr
                        key={person.id}
                        aria-selected={person.id === selected}
                        className={person.id === selected ? 'bg-chip-selected-bg' : undefined}
                      >
                        <td className={TD}>
                          <button
                            type="button"
                            onClick={() => onSelect(person.id === selected ? null : person.id)}
                            className="cursor-pointer border-0 bg-transparent p-0 text-left font-[inherit] font-medium text-ink underline-offset-2 hover:underline"
                          >
                            {person.first_name}
                          </button>
                        </td>
                        <td className={TD}>{person.email}</td>
                        <td className={`${TD} text-muted`}>{person.prop_firm || '—'}</td>
                        <td className={`${TD} num whitespace-nowrap text-muted`}>{utcDay(person.joined_at)}</td>
                        <td className={TD}>
                          <StateBadge person={person} now={now} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : null}
          {beta.data && total > BETA_PAGE ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-[13px]">
              <span className="text-muted">
                {first}–{last} of {total.toLocaleString('en-US')}
              </span>
              <span className="flex gap-2">
                <Button onClick={() => onQuery({ ...query, page: query.page - 1 })} disabled={query.page <= 1}>
                  Previous
                </Button>
                <Button onClick={() => onQuery({ ...query, page: query.page + 1 })} disabled={last >= total}>
                  Next
                </Button>
              </span>
            </div>
          ) : null}
        </Card>
        <aside className="flex min-w-0 flex-[2_1_360px] flex-col gap-4" aria-label="Selected person">
          {open ? (
            <Panel key={open.id} person={open} now={now} onRevoke={() => setRevoking(open)} />
          ) : (
            <Card className="px-5 py-4 text-muted">
              Choose a person to see their code, their 30 days and their emails.
            </Card>
          )}
        </aside>
      </div>
      {revoking && revokeOf ? (
        <RevokeDialog
          person={revoking}
          kind={revokeOf}
          onClose={() => setRevoking(null)}
          onDone={(done) => {
            setResult({ name: revoking.first_name, notice: done.notice })
            setRevoking(null)
          }}
        />
      ) : null}
    </>
  )
}
