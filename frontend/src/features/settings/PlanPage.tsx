import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Card, Spinner } from '../../components/ui/primitives'
import type { components } from '../../lib/api/schema'
import { daysLeft, pluralize } from '../../lib/format'
import { useRedeemCode } from '../../lib/grants'
import { useSession } from '../../lib/session'
import { messageOf } from '../tags/messages'
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

/** Use a promo code (or an older plan code). What the server says is what is shown: one sentence for every code that does
 * not work. */
export function RedeemCode() {
  const redeem = useRedeemCode()
  const [code, setCode] = useState('')
  const typed = code.trim()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!typed || redeem.isPending) return
    redeem.mutate(typed, { onSuccess: () => setCode('') })
  }

  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Redeem a promo code">
      <h2 className="m-0 text-base font-semibold">Have a promo code?</h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field id="plan-code" label="Promo code" hint="For example FREE30. Case and spaces do not matter.">
          <input
            id="plan-code"
            type="text"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={64}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className={CONTROL}
          />
        </Field>
        <div>
          <Button type="submit" variant="primary" disabled={!typed || redeem.isPending}>
            {redeem.isPending ? 'Redeeming…' : 'Redeem'}
          </Button>
        </div>
      </form>
      {redeem.isError ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {messageOf(redeem.error)}
        </p>
      ) : null}
      {redeem.isSuccess ? (
        <p role="status" className="m-0 text-[13px] text-pos">
          Done. You are on the {redeem.data.effective} plan
          {redeem.data.expires_at ? ` until ${when(redeem.data.expires_at)}` : ''}.
        </p>
      ) : null}
    </Card>
  )
}

/** Said once, on the way in from sign-up, when the promo code the account was made with could not be applied. */
export function PromoNotApplied() {
  return (
    <div role="status" className="rounded-[10px] border border-warn-line bg-raised px-4 py-3 text-[13px]">
      <strong>Your account is ready, but the promo code could not be applied.</strong> Its last use was taken a moment
      ago, or the free days you already have come first. If it still works when those end, enter it below.
    </div>
  )
}

/** Which plan the account is on, why, until when, and what it includes, and a place to use a promo code. `promoMissed`:
 * the person has just signed up with a promo code that could not be applied (`?promo=not_applied`). */
export function PlanPage({ promoMissed = false }: { promoMissed?: boolean }) {
  const session = useSession()
  if (session.isPending) return <Spinner label="Loading your plan" />
  if (!session.data) return null
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
      <p className="m-0 text-[13px] text-muted">
        Paid plans open after the beta. Until then, plans come from an invitation or a code.
      </p>
    </>
  )
}
