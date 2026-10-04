import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Modal } from '../../components/ui/Modal'
import { Button, Card } from '../../components/ui/primitives'
import { useCreateGrantCode, useCreateInvite, useRevokeCode, type GrantCode, type Revoke } from '../../lib/adminCodes'
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

/** A code that was just made. It is the only time it is ever shown: only its hash is kept. */
export function NewCode({ code, what, onDone }: { code: string; what: string; onDone: () => void }) {
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
      <p className="m-0 text-[13px] text-warn">
        This is the only time the code is shown. Only its hash is kept, so it cannot be looked up later: copy it now.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={copy}>Copy</Button>
        <Button variant="ghost" onClick={onDone}>
          I have copied it
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
                {g.label} ({g.days} days of Pro)
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

/** Make a plan code: a name, how many days of Pro it gives, how many people may use it, and when the code itself ends. */
export function CreateGrantCode({ onCreated }: { onCreated: (code: string) => void }) {
  const create = useCreateGrantCode()
  const [label, setLabel] = useState('')
  const [days, setDays] = useState('90')
  const [uses, setUses] = useState('')
  const [ends, setEnds] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (create.isPending) return
    const given = wholeNumber(days)
    const people = wholeNumber(uses)
    const ending = wholeNumber(ends)
    if (!label.trim()) return setProblem('Give it a label, so you can tell it apart later.')
    if (given === null || Number.isNaN(given) || given < 1 || given > 3650) {
      return setProblem('Days of Pro must be a whole number from 1 to 3650.')
    }
    if (people !== null && (Number.isNaN(people) || people < 1 || people > 1_000_000)) {
      return setProblem('People must be a whole number of at least 1, or empty for any number.')
    }
    if (ending !== null && (Number.isNaN(ending) || ending < 1 || ending > 3650)) {
      return setProblem('The code can end after 1 to 3650 days, or never if empty.')
    }
    setProblem(null)
    create.mutate(
      { label: label.trim(), days: given, maxRedemptions: people, expiresInDays: ending },
      {
        onSuccess: (made) => {
          onCreated(made.code)
          setLabel('')
          setDays('90')
          setUses('')
          setEnds('')
        },
      },
    )
  }

  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Make a plan code">
      <h2 className="m-0 text-base font-semibold">Make a plan code</h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field id="grant-label" label="Label" hint="What it is for, in your words. Only you see it.">
          <input
            id="grant-label"
            value={label}
            maxLength={100}
            onChange={(e) => setLabel(e.target.value)}
            className={CONTROL}
          />
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
            <Field id="grant-uses" label="How many people (optional)">
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
        <Field id="grant-ends" label="The code ends after (days, optional)">
          <input
            id="grant-ends"
            inputMode="numeric"
            value={ends}
            onChange={(e) => setEnds(e.target.value)}
            className={CONTROL}
          />
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
          {create.isPending ? 'Making…' : 'Make plan code'}
        </Button>
        <p className="m-0 text-xs text-muted">
          What Pro includes is not decided yet, so a plan code changes the label and the date and unlocks nothing yet.
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
  const what = kind === 'invite' ? 'invite' : 'plan code'

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
