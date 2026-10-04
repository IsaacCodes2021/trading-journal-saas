import { useState } from 'react'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import {
  actionText,
  formatBytes,
  useAdminUser,
  utcDay,
  utcMoment,
  type AdminUser,
  type AdminUserDetail,
} from '../../lib/admin'
import { useSession } from '../../lib/session'
import { messageOf } from '../tags/messages'
import { OverrideCard } from './OverrideCard'
import { ASKS, ReasonDialog, type Ask, type ReasonOnly } from './ReasonDialog'
import { PlanText, StatusBadge } from './parts'

function Fact({ label, children, note }: { label: string; children: React.ReactNode; note?: string }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="font-semibold">{children}</div>
      {note ? <div className="text-xs text-muted">{note}</div> : null}
    </div>
  )
}

/** Who may change what is the server's rule; this only hides what would be refused. */
function actionsFor(status: AdminUser['status']): ReasonOnly[] {
  if (status === 'active') return ['suspend', 'revoke-sessions', 'schedule-deletion']
  if (status === 'suspended') return ['unsuspend', 'revoke-sessions']
  return ['cancel-deletion']
}

function Account({ detail, mine }: { detail: AdminUserDetail; mine: boolean }) {
  const { user } = detail
  const [asking, setAsking] = useState<Ask | null>(null)
  const locked = user.is_admin || mine
  return (
    <>
      <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Account">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="m-0 text-base font-semibold break-all">{user.email}</h2>
          <StatusBadge status={user.status} />
          {user.is_admin ? (
            <span className="inline-flex h-[22px] items-center rounded-full border border-warn-line px-2 text-xs text-warn">
              Operator
            </span>
          ) : null}
        </div>
        <div className="text-xs text-muted">
          Joined {utcDay(user.created_at)} ·{' '}
          {user.last_login_at ? `last sign-in ${utcDay(user.last_login_at)}` : 'never signed in'} ·{' '}
          {detail.active_sessions} active {detail.active_sessions === 1 ? 'session' : 'sessions'}
        </div>
        <div className="flex flex-wrap gap-x-7 gap-y-3">
          <Fact
            label="Effective plan"
            note={user.plan.source === 'free' ? undefined : `from ${PlanText.source(user.plan.source)}`}
          >
            {user.plan.effective === 'pro' ? 'Pro' : 'Free'}
          </Fact>
          {user.plan.expires_at ? <Fact label="Plan ends">{utcDay(user.plan.expires_at)}</Fact> : null}
          <Fact label="Accounts">{user.usage.accounts}</Fact>
          <Fact label="Trades">{user.usage.trades.toLocaleString('en-US')}</Fact>
          <Fact label="Storage used">{formatBytes(user.usage.storage_bytes)}</Fact>
          {user.deletion_scheduled_at ? (
            <Fact label="Deleted on" note="unless they sign in first">
              {utcDay(user.deletion_scheduled_at)}
            </Fact>
          ) : null}
        </div>
        {locked ? (
          <p className="m-0 rounded-lg border border-line px-3 py-2 text-[13px] text-muted" role="note">
            {mine
              ? 'This is your own account. You cannot change it here.'
              : 'This account is an operator. Operators are changed with the command line, not here.'}
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {actionsFor(user.status).map((kind) => (
              <Button
                key={kind}
                onClick={() => setAsking(ASKS[kind])}
                className={ASKS[kind].danger ? 'border-neg text-neg' : undefined}
              >
                {ASKS[kind].confirm}
                {ASKS[kind].danger && kind === 'schedule-deletion' ? '…' : ''}
              </Button>
            ))}
          </div>
        )}
        <p className="m-0 text-xs text-muted">
          Counters only: trades, notes and screenshots are never shown here. Opening this page is recorded in the audit
          log.
        </p>
      </Card>
      {asking ? (
        <ReasonDialog ask={asking} userId={user.id} email={user.email} onClose={() => setAsking(null)} />
      ) : null}
      {locked ? null : <OverrideCard detail={detail} />}
      <Card className="p-0" role="region" aria-label="Override history">
        <h2 className="m-0 border-b border-line px-5 py-3 text-[13px] font-semibold tracking-wide text-muted uppercase">
          Override history
        </h2>
        {detail.overrides.length === 0 && detail.redemptions.length === 0 ? (
          <p className="m-0 px-5 py-3.5 text-[13px] text-muted">None yet.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-2.5 p-0 px-5 py-3.5 text-[13px]">
            {detail.overrides.map((o) => (
              <li key={`o${o.id}`}>
                <div>
                  <span className="num">{utcDay(o.created_at)}</span> · {o.plan === 'pro' ? 'Pro' : 'Free'}
                  {o.expires_at ? ` until ${utcDay(o.expires_at)}` : ''}{' '}
                  <span className="text-muted">
                    {o.revoked_at ? `· revoked ${utcDay(o.revoked_at)}` : '· in force'}
                  </span>
                </div>
                <div className="text-muted">{o.reason}</div>
                {o.revoke_reason ? <div className="text-muted">Revoked: {o.revoke_reason}</div> : null}
              </li>
            ))}
            {detail.redemptions.map((r, i) => (
              <li key={`r${i}`}>
                <div>
                  <span className="num">{utcDay(r.starts_at)}</span> · {r.plan === 'pro' ? 'Pro' : r.plan} until{' '}
                  {utcDay(r.ends_at)} <span className="text-muted">· plan code</span>
                </div>
                <div className="text-muted">{r.code_label || '(no label)'}</div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card className="p-0" role="region" aria-label="Recent events">
        <h2 className="m-0 border-b border-line px-5 py-3 text-[13px] font-semibold tracking-wide text-muted uppercase">
          Recent events
        </h2>
        {detail.events.length === 0 ? (
          <p className="m-0 px-5 py-3.5 text-[13px] text-muted">Nothing yet.</p>
        ) : (
          <ul className="m-0 list-none divide-y divide-line-soft p-0">
            {detail.events.map((e) => (
              <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-x-4 px-5 py-2.5 text-[13px]">
                <span>
                  {actionText(e.action)}
                  {e.actor === 'admin' ? <span className="text-muted"> · by an operator</span> : null}
                  {typeof e.meta['reason'] === 'string' ? (
                    <span className="text-muted"> · {String(e.meta['reason'])}</span>
                  ) : null}
                </span>
                <time dateTime={e.at} className="text-xs text-muted">
                  {utcMoment(e.at)}
                </time>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}

/** One account in full: facts, what can be done, its plan override, and its history. */
export function UserPanel({ id }: { id: string }) {
  const detail = useAdminUser(id)
  const session = useSession()
  if (detail.isPending) return <Spinner label="Loading the account" />
  if (detail.isError)
    return (
      <Card className="px-5 py-4 text-neg" role="alert">
        {messageOf(detail.error)}
      </Card>
    )
  return <Account detail={detail.data} mine={session.data?.user.id === id} />
}
