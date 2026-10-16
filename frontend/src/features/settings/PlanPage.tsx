import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Card, Spinner } from '../../components/ui/primitives'
import type { components } from '../../lib/api/schema'
import { isSubscribed, useBilling } from '../../lib/billing'
import { daysLeft, pluralize } from '../../lib/format'
import { useSession } from '../../lib/session'
import { BillingPlan } from './BillingPlan'
import { PromoNotApplied, RedeemCode, when } from './PromoCode'
import { SectionHeader } from './SettingsLayout'

type Plan = components['schemas']['PlanOut']

/** How long the page keeps asking, back from the checkout, before it says the payment is not confirmed yet. */
export const CONFIRM_WAIT_MS = 60_000

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

/**
 * Which plan the account is on, why, until when, and a place to use a promo code. While billing is on, the billing
 * version of the page (`BillingPlan`); while it is off, what it always showed: the plan and what it includes.
 * `promoMissed`: the person has just signed up with a promo code that could not be applied (`?promo=not_applied`).
 * `checkoutDone`: the person is back from the payment page (`?checkout=done`); the page asks every 2 seconds for a minute
 * until the subscription shows.
 */
export function PlanPage({
  promoMissed = false,
  promoSaved = false,
  checkoutDone = false,
}: {
  promoMissed?: boolean
  promoSaved?: boolean
  checkoutDone?: boolean
}) {
  const session = useSession()
  const queryClient = useQueryClient()
  const [late, setLate] = useState(false)
  const billing = useBilling({ poll: checkoutDone && !late })
  const confirmed = checkoutDone && isSubscribed(billing.data)

  useEffect(() => {
    if (!checkoutDone) return
    const timer = setTimeout(() => setLate(true), CONFIRM_WAIT_MS)
    return () => clearTimeout(timer)
  }, [checkoutDone])
  useEffect(() => {
    if (confirmed) void queryClient.invalidateQueries({ queryKey: ['session'] }) // the plan everywhere else, too
  }, [confirmed, queryClient])

  if (session.isPending || billing.isPending) return <Spinner label="Loading your plan" />
  if (!session.data) return null
  if (billing.data) {
    return (
      <BillingPlan
        billing={billing.data}
        source={session.data.plan.source}
        promoMissed={promoMissed}
        promoSaved={promoSaved}
        checkout={checkoutDone ? (late ? 'late' : 'waiting') : null}
      />
    )
  }
  const { plan, entitlements } = session.data
  const left = daysLeft(plan.expires_at)
  const entries = Object.entries(entitlements)
  return (
    <>
      <SectionHeader title="Plan & billing">What you have, and what it includes.</SectionHeader>
      {promoMissed ? <PromoNotApplied /> : null}
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
      <RedeemCode />
      {billing.isError ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          Your billing details could not be loaded. Reload the page to try again.
        </p>
      ) : (
        <p className="m-0 text-[13px] text-muted">
          Paid plans open after the beta. Until then, plans come from an invitation or a code.
        </p>
      )}
    </>
  )
}
