import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Card } from '../../components/ui/primitives'
import { useChangeUser, utcDay, utcMoment, type AdminUserDetail } from '../../lib/admin'
import { messageOf } from '../tags/messages'

/** "2026-12-31" as the last moment of that day in UTC, so an override "until Dec 31" lasts through Dec 31. */
export function endOfDay(date: string): string {
  return `${date}T23:59:59Z`
}

/** Give the person a plan whatever else they have, or take the override away. Both need a reason, kept in the log. */
export function OverrideCard({ detail }: { detail: AdminUserDetail }) {
  const change = useChangeUser()
  const [plan, setPlan] = useState<'free' | 'pro'>('pro')
  const [until, setUntil] = useState('')
  const [reason, setReason] = useState('')
  const typed = reason.trim()
  const active = detail.overrides.find((o) => o.revoked_at === null) ?? null
  const id = detail.user.id

  const save = (event: FormEvent) => {
    event.preventDefault()
    if (!typed || change.isPending) return
    change.mutate(
      { id, change: { kind: 'set-override', plan, expiresAt: until ? endOfDay(until) : null, reason: typed } },
      { onSuccess: () => setReason('') },
    )
  }
  const revoke = () => {
    if (!typed || change.isPending) return
    change.mutate({ id, change: { kind: 'revoke-override', reason: typed } }, { onSuccess: () => setReason('') })
  }

  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Plan override">
      <h2 className="m-0 text-[13px] font-semibold tracking-wide text-muted uppercase">Plan override</h2>
      {active ? (
        <p className="m-0 text-[13px]">
          In force: <strong>{active.plan}</strong>
          {active.expires_at ? ` until ${utcDay(active.expires_at)}` : ', with no end'}. Set{' '}
          {utcMoment(active.created_at)}.
        </p>
      ) : (
        <p className="m-0 text-[13px] text-muted">
          No override. The plan comes from a plan code, a subscription, or is free.
        </p>
      )}
      <form onSubmit={save} className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-3">
          <div className="min-w-[140px] flex-1">
            <Field id="override-plan" label="Plan">
              <select
                id="override-plan"
                value={plan}
                onChange={(e) => setPlan(e.target.value === 'free' ? 'free' : 'pro')}
                className={CONTROL}
              >
                <option value="pro">Pro</option>
                <option value="free">Free</option>
              </select>
            </Field>
          </div>
          <div className="min-w-[140px] flex-1">
            <Field id="override-until" label="Ends (optional)">
              <input
                id="override-until"
                type="date"
                value={until}
                onChange={(e) => setUntil(e.target.value)}
                className={CONTROL}
              />
            </Field>
          </div>
        </div>
        <Field id="override-reason" label="Reason (required)">
          <textarea
            id="override-reason"
            rows={3}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className={`${CONTROL} h-auto py-2`}
          />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={!typed || change.isPending}>
            {change.isPending ? 'Saving…' : 'Save override'}
          </Button>
          {active ? (
            <Button onClick={revoke} disabled={!typed || change.isPending}>
              Revoke override
            </Button>
          ) : null}
        </div>
        {change.isError ? (
          <p role="alert" className="m-0 text-[13px] text-neg">
            {messageOf(change.error)}
          </p>
        ) : null}
        <p className="m-0 text-xs text-muted">
          An override wins over a plan code and a subscription, and billing never changes it. The reason is kept in the
          audit log and is never shown to the person.
        </p>
      </form>
    </Card>
  )
}
