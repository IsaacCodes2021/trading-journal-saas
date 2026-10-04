import type { AdminUser } from '../../lib/admin'

const STATUS: Record<AdminUser['status'], { text: string; className: string }> = {
  active: { text: 'Active', className: 'bg-long-bg text-pos' },
  suspended: { text: 'Suspended', className: 'bg-short-bg text-neg' },
  pending_deletion: { text: 'Pending deletion', className: 'bg-raised text-warn' },
}

/** An account's status as a small badge. */
export function StatusBadge({ status }: { status: AdminUser['status'] }) {
  const { text, className } = STATUS[status]
  return (
    <span
      className={`inline-flex h-[22px] items-center rounded-md px-2 text-xs font-medium whitespace-nowrap ${className}`}
    >
      {text}
    </span>
  )
}

const SOURCES: Record<AdminUser['plan']['source'], string> = {
  free: 'the free plan',
  override: 'an operator override',
  grant: 'a plan code',
  subscription: 'a subscription',
}

/** Where a plan came from, in words. */
export const PlanText = {
  source: (source: AdminUser['plan']['source']): string => SOURCES[source],
}

/** "Pro (override)" for a table cell. */
export function PlanCell({ plan }: { plan: AdminUser['plan'] }) {
  const short = { free: '', override: 'override', grant: 'plan code', subscription: 'subscription' }[plan.source]
  return (
    <>
      {plan.effective === 'pro' ? 'Pro' : 'Free'}
      {short ? <span className="text-muted"> ({short})</span> : null}
    </>
  )
}
