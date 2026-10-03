import { Card, Spinner } from '../../components/ui/primitives'
import type { components } from '../../lib/api/schema'
import { daysLeft, pluralize } from '../../lib/format'
import { useSession } from '../../lib/session'
import { SectionHeader } from './SettingsLayout'

type Plan = components['schemas']['PlanOut']

const SOURCE_TEXT: Record<Plan['source'], string> = {
  free: 'The free plan.',
  grant: 'Given to you by an invitation or a code.',
  subscription: 'A paid subscription.',
  override: 'Set for you by the team.',
}

/** "max_accounts" to "Max accounts". */
function humanize(key: string): string {
  const spaced = key.replaceAll('_', ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

function valueText(value: number | boolean): string {
  if (typeof value === 'boolean') return value ? 'Included' : 'Not included'
  return value.toLocaleString('en-US')
}

function when(iso: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(iso),
  )
}

/** Which plan the account is on, why, until when, and what it includes. Read-only: payments arrive after the beta. */
export function PlanPage() {
  const session = useSession()
  if (session.isPending) return <Spinner label="Loading your plan" />
  if (!session.data) return null
  const { plan, entitlements } = session.data
  const left = daysLeft(plan.expires_at)
  const entries = Object.entries(entitlements)
  return (
    <>
      <SectionHeader title="Plan & billing">What you have, and what it includes.</SectionHeader>
      <Card className="flex flex-col gap-1 px-5 py-4" role="region" aria-label="Your plan">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="m-0 text-xl font-semibold capitalize">{plan.effective}</h2>
          {left !== null ? <span className="text-warn">{pluralize(left, 'day')} left</span> : null}
        </div>
        <p className="m-0 text-muted">{SOURCE_TEXT[plan.source]}</p>
        {plan.expires_at ? (
          <p className="m-0 text-muted">
            {left === null ? 'Ended' : 'Ends'} {when(plan.expires_at)}.
          </p>
        ) : null}
      </Card>
      <Card className="p-0" role="region" aria-label="What your plan includes">
        <h2 className="m-0 border-b border-line px-5 py-3.5 text-base font-semibold">What it includes</h2>
        {entries.length === 0 ? (
          <p className="m-0 px-5 py-4 text-muted">Nothing is limited on this plan.</p>
        ) : (
          <dl className="m-0 divide-y divide-line-soft">
            {entries.map(([key, value]) => (
              <div key={key} className="flex flex-wrap justify-between gap-3 px-5 py-3">
                <dt className="text-muted">{humanize(key)}</dt>
                <dd className="num m-0">{valueText(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </Card>
      <p className="m-0 text-[13px] text-muted">
        Paid plans open after the beta. Until then, plans come from an invitation or a code.
      </p>
    </>
  )
}
