import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Button, Card } from '../../components/ui/primitives'
import {
  promoCodeOf,
  promoPreview,
  useCreateGrantCode,
  useCreateInvite,
  useRevokeCode,
  type GrantCode,
  type Revoke,
} from '../../lib/adminCodes'
import { messageOf } from '../tags/messages'

/** The number in a box, or null for an empty one. Anything that is not a whole number is NaN, which the caller refuses. */
export function wholeNumber(text: string): number | null {
  const typed = text.trim()
  if (typed === '') return null
  return /^\d{1,9}$/.test(typed) ? Number(typed) : Number.NaN
}

/** What a revoke dialog is about. */
export interface Dialog {
  kind: Revoke['kind']
  id: number
  label: string
}

/** A code that was just made. A random code (and an invite) is shown this once: only its hash is kept. A typed promo code
 * (`kept`) stays readable in the table. */
export function NewCode({
  code,
  what,
  kept = false,
  onDone,
}: {
  code: string
  what: string
  kept?: boolean
  onDone: () => void
}) {
  const [copied, setCopied] = useState<'yes' | 'no' | null>(null)
  const copy = () => {
    navigator.clipboard.writeText(code).then(
      () => setCopied('yes'),
      () => setCopied('no'),
    )
  }
  return (
    <Card className="flex flex-col gap-2 border-warn-line px-5 py-4" role="region" aria-label="New code">
      <h2 className="m-0 text-base font-semibold">{what}</h2>
      <p className="num m-0 text-xl font-semibold tracking-wide break-all" data-testid="new-code">
        {code}
      </p>
      {kept ? (
        <p className="m-0 text-[13px] text-muted">
          It works now. It stays in the table below, so you can copy it again any time.
        </p>
      ) : (
        <p className="m-0 text-[13px] text-warn">
          This is the only time the code is shown. Only its hash is kept, so it cannot be looked up later: copy it now.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={copy}>Copy</Button>
        <Button variant="ghost" onClick={onDone}>
          {kept ? 'Done' : 'I have copied it'}
        </Button>
        {copied === 'yes' ? (
          <span role="status" className="text-[13px] text-pos">
            Copied.
          </span>
        ) : null}
        {copied === 'no' ? (
          <span role="alert" className="text-[13px] text-neg">
            Could not copy. Select the code and copy it by hand.
          </span>
        ) : null}
      </div>
    </Card>
  )
}

/** Make an invite: who it is for (optional), how many may use it, how long it lasts, and the plan it carries (optional). */
export function CreateInvite({ working, onCreated }: { working: GrantCode[]; onCreated: (code: string) => void }) {
  const create = useCreateInvite()
  const [label, setLabel] = useState('')
  const [email, setEmail] = useState('')
  const [uses, setUses] = useState('1')
  const [days, setDays] = useState('')
  const [grant, setGrant] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (create.isPending) return
    const maxUses = wholeNumber(uses)
    const lasts = wholeNumber(days)
    if (maxUses === null || Number.isNaN(maxUses) || maxUses < 1 || maxUses > 1000) {
      return setProblem('Uses must be a whole number from 1 to 1000.')
    }
    if (lasts !== null && (Number.isNaN(lasts) || lasts < 1 || lasts > 365)) {
      return setProblem('Days must be a whole number from 1 to 365, or empty for no end.')
    }
    setProblem(null)
    create.mutate(
      {
        label: label.trim(),
        email: email.trim() || null,
        maxUses,
        days: lasts,
        grantCodeId: grant ? Number(grant) : null,
      },
      {
        onSuccess: (made) => {
          onCreated(made.code)
          setLabel('')
          setEmail('')
          setUses('1')
          setDays('')
          setGrant('')
        },
      },
    )
  }

  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Make an invite">
      <h2 className="m-0 text-base font-semibold">Make an invite</h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field id="invite-label" label="Label" hint="Who it is for, in your words. Only you see it.">
          <input
            id="invite-label"
            value={label}
            maxLength={100}
            onChange={(e) => setLabel(e.target.value)}
            className={CONTROL}
          />
        </Field>
        <Field id="invite-email" label="Only this email address (optional)">
          <input
            id="invite-email"
            type="email"
            autoComplete="off"
            value={email}
            maxLength={320}
            onChange={(e) => setEmail(e.target.value)}
            className={CONTROL}
          />
        </Field>
        <div className="flex flex-wrap gap-3">
          <div className="min-w-[110px] flex-1">
            <Field id="invite-uses" label="How many may use it">
              <input
                id="invite-uses"
                inputMode="numeric"
                value={uses}
                onChange={(e) => setUses(e.target.value)}
                className={CONTROL}
              />
            </Field>
          </div>
          <div className="min-w-[110px] flex-1">
            <Field id="invite-days" label="Works for (days, optional)">
              <input
                id="invite-days"
                inputMode="numeric"
                value={days}
                onChange={(e) => setDays(e.target.value)}
                className={CONTROL}
              />
            </Field>
          </div>
        </div>
        <Field
          id="invite-grant"
          label="Plan it carries (optional)"
          hint="They start on this plan when they sign up with it."
        >
          <select id="invite-grant" value={grant} onChange={(e) => setGrant(e.target.value)} className={CONTROL}>
            <option value="">None</option>
            {working.map((g) => (
              <option key={g.id} value={g.id}>
                {g.code ?? g.label} ({g.days} days of Pro)
              </option>
            ))}
          </select>
        </Field>
        {problem ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {problem}
          </p>
        ) : null}
        {create.isError ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {messageOf(create.error)}
          </p>
        ) : null}
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? 'Making…' : 'Make invite'}
        </Button>
      </form>
    </Card>
  )
}

/** The day after today, as YYYY-MM-DD in UTC: the earliest a code can be set to stop. */
function today(): string {
  return new Date().toISOString().slice(0, 10)
}

/**
 * Make a promo code: the code people type (or a random one), a note, what it gives (days of Pro; discounts switch on with
 * the payment provider), how many people may use it, and the last day it works. A typed code cannot be edited or reused
 * later, so the form says what people will be told before it is made.
 */
export function CreateGrantCode({ onCreated }: { onCreated: (code: string, kept: boolean) => void }) {
  const create = useCreateGrantCode()
  const [code, setCode] = useState('')
  const [label, setLabel] = useState('')
  const [days, setDays] = useState('30')
  const [uses, setUses] = useState('')
  const [ends, setEnds] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const typed = code.trim()
  const promo = typed ? promoCodeOf(typed) : null
  const given = wholeNumber(days)
  const people = wholeNumber(uses)
  const previewable =
    given !== null && !Number.isNaN(given) && given >= 1 && given <= 30 && (people === null || !Number.isNaN(people))

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (create.isPending) return
    if (typed && promo === null) {
      return setProblem('A promo code is 3 to 20 letters and digits, with no spaces or dashes, such as FREE30.')
    }
    if (!typed && !label.trim()) return setProblem('A random code needs a note, so you can tell it apart later.')
    if (given === null || Number.isNaN(given) || given < 1 || given > 30) {
      return setProblem('Days of Pro must be a whole number from 1 to 30.')
    }
    if (people !== null && (Number.isNaN(people) || people < 1 || people > 1_000_000)) {
      return setProblem('Most people must be a whole number of at least 1, or empty for any number.')
    }
    if (ends && ends < today()) return setProblem('That day has already passed. Choose a day from today on.')
    setProblem(null)
    create.mutate(
      { code: promo, label: label.trim(), days: given, maxRedemptions: people, expiresOn: ends || null },
      {
        onSuccess: (made) => {
          onCreated(made.code, made.grant_code.code !== null)
          setCode('')
          setLabel('')
          setDays('30')
          setUses('')
          setEnds('')
        },
      },
    )
  }

  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Make a promo code">
      <h2 className="m-0 text-base font-semibold">Make a promo code</h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field
          id="grant-code"
          label="Code"
          hint="3 to 20 letters and numbers, such as FREE30. Leave it blank to make a random one."
        >
          <input
            id="grant-code"
            value={code}
            maxLength={20}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            className={`${CONTROL} font-mono tracking-wide uppercase`}
          />
        </Field>
        <Field id="grant-label" label="Note to yourself" hint="Where you shared it, or who it is for. Only you see it.">
          <input
            id="grant-label"
            value={label}
            maxLength={100}
            onChange={(e) => setLabel(e.target.value)}
            className={CONTROL}
          />
        </Field>
        <Field id="grant-kind" label="Type">
          <select id="grant-kind" value="free_days" onChange={() => undefined} className={CONTROL}>
            <option value="free_days">Free days of Pro</option>
            <option value="discount" disabled>
              Discount (switches on once a payment provider is connected)
            </option>
          </select>
        </Field>
        <div className="flex flex-wrap gap-3">
          <div className="min-w-[110px] flex-1">
            <Field id="grant-days" label="Days of Pro">
              <input
                id="grant-days"
                inputMode="numeric"
                value={days}
                onChange={(e) => setDays(e.target.value)}
                className={CONTROL}
              />
            </Field>
          </div>
          <div className="min-w-[110px] flex-1">
            <Field id="grant-uses" label="Most people (optional)">
              <input
                id="grant-uses"
                inputMode="numeric"
                value={uses}
                onChange={(e) => setUses(e.target.value)}
                className={CONTROL}
              />
            </Field>
          </div>
        </div>
        <Field id="grant-ends" label="Stops working after (optional)" hint="The last day it works, in UTC.">
          <input
            id="grant-ends"
            type="date"
            value={ends}
            onChange={(e) => setEnds(e.target.value)}
            className={CONTROL}
          />
        </Field>
        {previewable ? (
          <div className="rounded-lg border border-line bg-bg px-3.5 py-2.5 text-[13px]" aria-live="polite">
            <div className="text-xs text-muted">What people will see</div>
            <p className="m-0 mt-1">{promoPreview({ code: promo, days: given, people, endsOn: ends || null })}</p>
          </div>
        ) : null}
        {problem ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {problem}
          </p>
        ) : null}
        {create.isError ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {messageOf(create.error)}
          </p>
        ) : null}
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? 'Making…' : 'Make code'}
        </Button>
        <p className="m-0 text-xs text-muted">
          A code is kept readable and cannot be edited: revoke it and make another. A code is never reused, even after
          it is revoked.
        </p>
      </form>
    </Card>
  )
}

/** Ask why, then stop an invite or a plan code being used. People who already used it keep what they got. */
export function RevokeDialog({
  kind,
  id,
  label,
  onClose,
}: {
  kind: Revoke['kind']
  id: number
  label: string
  onClose: () => void
}) {
  const revoke = useRevokeCode()
  const [reason, setReason] = useState('')
  const typed = reason.trim()
  const what = kind === 'invite' ? 'invite' : 'code'

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!typed || revoke.isPending) return
    revoke.mutate({ kind, id, reason: typed }, { onSuccess: onClose })
  }

  return (
    <Modal
      label={`Revoke this ${what}?`}
      onClose={revoke.isPending ? () => undefined : onClose}
      className="max-w-[480px]"
    >
      <form onSubmit={submit} className="flex flex-col gap-3.5">
        <h2 className="m-0 text-base font-semibold">Revoke this {what}?</h2>
        <p className="m-0 text-muted">
          <strong className="text-ink">{label || '(no label)'}</strong>. It stops working now. People who already used
          it keep what they got.
        </p>
        <Field id="revoke-reason" label="Why? (kept in the audit log)">
          <textarea
            id="revoke-reason"
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
            {revoke.isPending ? 'Working…' : `Revoke ${what}`}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
