import { useState, type ReactNode } from 'react'
import { Modal } from '../../components/ui/Modal'
import { Button, Card } from '../../components/ui/primitives'
import type { components } from '../../lib/api/schema'
import {
  billingDay,
  isPaidUp,
  priceOf,
  useCancelSubscription,
  useManageBilling,
  useResumeSubscription,
  useSubscribe,
  type Billing,
} from '../../lib/billing'
import { EXPORT_URL } from '../../lib/exportData'
import { daysLeft, pluralize } from '../../lib/format'
import { messageOf } from '../tags/messages'
import { DANGER } from './DeleteAccount'
import { PromoNotApplied, PromoSaved, RedeemCode } from './PromoCode'
import { SectionHeader } from './SettingsLayout'

type PlanSource = components['schemas']['PlanOut']['source']

/** Where the published billing and refund policy lives (the marketing site). */
export const REFUNDS_URL = 'https://roundturn.app/refunds/'

/** Back from the checkout: still waiting for the provider to confirm, or a minute has passed without word. */
export type CheckoutReturn = 'waiting' | 'late' | null

const NO_REFUNDS = 'Cancel any time on this page. Payments are not refunded.'
const TAX = 'Taxes, where they apply, are added at checkout.'

/** "Oct 21", or the words given when the server sent no date (it always should). */
function day(iso: string | null, otherwise = 'the end of the period'): string {
  return billingDay(iso) || otherwise
}

function Problem({ error }: { error: unknown }) {
  return (
    <p role="alert" className="m-0 text-[13px] text-neg">
      {messageOf(error)}
    </p>
  )
}

function Divider() {
  return <div aria-hidden="true" className="h-px bg-line-soft" />
}

/** The price and the Subscribe button, and when billing would start. */
function SubscribeOffer({ billing, title, note }: { billing: Billing; title: string; note: string }) {
  const subscribe = useSubscribe()
  const price = priceOf(billing)
  const leaving = subscribe.isPending || subscribe.isSuccess // on its way to the payment page
  return (
    <>
      <Divider />
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <span className="font-semibold">{title}</span>
          <span className="num text-[15px]">
            {price.amount} <span className="font-sans text-sm text-muted">{price.per}</span>
          </span>
        </div>
        <Button variant="primary" onClick={() => subscribe.mutate()} disabled={leaving}>
          {leaving ? 'Opening checkout…' : 'Subscribe'}
        </Button>
      </div>
      {subscribe.isError ? <Problem error={subscribe.error} /> : null}
      {billing.discount ? (
        <p className="m-0 text-[13px] text-pos" role="note">
          Your discount code is saved: {billing.discount.words}, applied at checkout.
        </p>
      ) : null}
      <p className="m-0 text-[13px] text-muted">
        {note} {TAX} {NO_REFUNDS}
      </p>
    </>
  )
}

/** Opens the provider's page for the card, invoices and receipts. */
function ManageBilling({ label = 'Manage billing', primary = false }: { label?: string; primary?: boolean }) {
  const manage = useManageBilling()
  return (
    <>
      <Button
        variant={primary ? 'primary' : 'default'}

        onClick={() => manage.mutate()}
        disabled={manage.isPending}
      >
        {manage.isPending ? 'Opening…' : label}
      </Button>
      {manage.isError ? <Problem error={manage.error} /> : null}
    </>
  )
}

function CancelDialog({ billing, onClose }: { billing: Billing; onClose: () => void }) {
  const cancel = useCancelSubscription()
  const until = day(billing.renews_at)
  return (
    <Modal
      label="Cancel your subscription"
      onClose={cancel.isPending ? () => undefined : onClose}
      className="max-w-[480px] gap-3.5 p-6"
    >
      <h2 className="m-0 text-lg font-semibold">Cancel your subscription?</h2>
      <p className="m-0">
        You keep full access until <strong>{until}</strong>.{' '}
        {billing.first_charge_pending
          ? 'Your subscription will not start, and nothing is charged.'
          : 'Your subscription will not renew, and payments are not refunded.'}
      </p>
      <p className="m-0 text-muted">
        After {until} your journal is read-only: everything stays, to look at and download. You can subscribe again at
        any time.
      </p>
      {cancel.isError ? <Problem error={cancel.error} /> : null}
      <div className="flex flex-wrap justify-end gap-2.5 pt-1.5">
        <Button onClick={onClose} disabled={cancel.isPending} data-autofocus>
          Keep subscription
        </Button>
        <Button
          className={DANGER}
          onClick={() => cancel.mutate(undefined, { onSuccess: onClose })}
          disabled={cancel.isPending}
        >
          {cancel.isPending ? 'Cancelling…' : 'Cancel subscription'}
        </Button>
      </div>
    </Modal>
  )
}

function Active({ billing }: { billing: Billing }) {
  const [asking, setAsking] = useState(false)
  const price = priceOf(billing)
  const renews = day(billing.renews_at)
  return (
    <>
      <Heading title="Pro">
        <span className="num text-muted">
          {price.amount} {price.per}
        </span>
      </Heading>
      <p className="m-0 text-muted">
        {billing.first_charge_pending
          ? `Your first payment is on ${renews}, when your free days end.`
          : `Renews on ${renews}.`}{' '}
        {TAX}
      </p>
      {billing.can_manage ? (
        <div className="flex flex-wrap items-center gap-3">
          <ManageBilling />
          <span className="text-[13px] text-muted">
            Your card, invoices and receipts, on the payment provider’s page.
          </span>
        </div>
      ) : null}
      <Divider />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-[13px] text-muted">
          Cancel any time. You keep Pro until {renews}, and nothing is deleted.
        </span>
        <Button className="text-neg!" onClick={() => setAsking(true)}>
          Cancel subscription
        </Button>
      </div>
      {asking ? <CancelDialog billing={billing} onClose={() => setAsking(false)} /> : null}
    </>
  )
}

function Cancelling({ billing }: { billing: Billing }) {
  const resume = useResumeSubscription()
  const ends = day(billing.ends_at)
  return (
    <>
      <Heading title="Pro">
        <span className="text-warn">Ends {ends}</span>
      </Heading>
      <p className="m-0 text-muted">
        Your subscription will not renew. You keep full access until {ends}. After that your journal is read-only:
        everything stays, to look at and download.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"

          onClick={() => resume.mutate()}
          disabled={resume.isPending}
        >
          {resume.isPending ? 'Resuming…' : 'Resume subscription'}
        </Button>
        {billing.can_manage ? <ManageBilling /> : null}
      </div>
      {resume.isError ? <Problem error={resume.error} /> : null}
      <p className="m-0 text-[13px] text-muted">
        Resume before {ends} and it renews as before, with nothing charged now.
      </p>
    </>
  )
}

function PastDue({ billing }: { billing: Billing }) {
  const until = day(billing.ends_at)
  return (
    <>
      <Heading title="Pro">
        <span className="text-warn">Payment failed</span>
      </Heading>
      <p className="m-0 text-muted">
        Your last payment did not go through, and the payment provider will try your card again. You keep full access
        until {until}. If no payment goes through by then, your journal becomes read-only until one does. Nothing is
        deleted.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <ManageBilling label="Update payment method" primary />
        <span className="text-[13px] text-muted">Opens the payment provider’s page.</span>
      </div>
    </>
  )
}

function Trial({ billing }: { billing: Billing }) {
  const left = daysLeft(billing.ends_at)
  const ends = day(billing.ends_at, 'the end of the trial')
  return (
    <>
      <Heading title="Free trial">
        {left !== null ? (
          <span className={left <= 3 ? 'text-warn' : 'text-muted'}>{pluralize(left, 'day')} left</span>
        ) : null}
      </Heading>
      <p className="m-0 text-muted">Full access to everything until {ends}. No card needed.</p>
      <SubscribeOffer
        billing={billing}
        title="Roundturn Pro"
        note={
          billing.billing_starts_at
            ? `Billing starts on ${day(billing.billing_starts_at)}, when your trial ends.`
            : 'Billing starts when you subscribe and renews monthly.'
        }
      />
    </>
  )
}

/** Pro from an invitation, a code or the team (an override). With no end date there is nothing to subscribe to. */
function Covered({ billing, source }: { billing: Billing; source: PlanSource }) {
  const left = daysLeft(billing.ends_at)
  const from = source === 'override' ? 'Set for you by the team' : 'From an invitation or a code'
  const ends = billingDay(billing.ends_at)
  return (
    <>
      <Heading title="Pro">
        {left !== null ? <span className="text-muted">{pluralize(left, 'day')} left</span> : null}
      </Heading>
      <p className="m-0 text-muted">{ends ? `${from}, until ${ends}.` : `${from}.`}</p>
      {billing.can_subscribe ? (
        <SubscribeOffer
          billing={billing}
          title={ends ? `Keep Pro after ${ends}` : 'Roundturn Pro'}
          note={
            billing.billing_starts_at
              ? `Billing starts on ${day(billing.billing_starts_at)}, when your free days end, so you are never charged for days you already have.`
              : 'Billing starts when you subscribe and renews monthly.'
          }
        />
      ) : null}
    </>
  )
}

const ENDED: Record<NonNullable<Billing['reason']>, string> = {
  expired: 'Your free days ended',
  revoked: 'Your access was ended',
  lapsed: 'Your subscription ended',
}

function ReadOnly({ billing }: { billing: Billing }) {
  const ended = billing.reason && billing.ends_at ? `${ENDED[billing.reason]} on ${day(billing.ends_at)}. ` : ''
  return (
    <>
      <h2 className="m-0 text-xl font-semibold">Read-only</h2>
      <p className="m-0 text-muted">
        {ended}Your journal is all still here: you can look at every trade, note and report, and{' '}
        <a href={EXPORT_URL} download>
          download your data
        </a>
        . Subscribe to import and edit again.
      </p>
      <SubscribeOffer
        billing={billing}
        title="Roundturn Pro"
        note="Billing starts when you subscribe and renews monthly."
      />
    </>
  )
}

function Heading({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-3">
      <h2 className="m-0 text-xl font-semibold">{title}</h2>
      {children}
    </div>
  )
}

function PlanCard({ billing, source }: { billing: Billing; source: PlanSource }) {
  return (
    <Card
      className={`flex flex-col gap-3.5 px-5 py-[18px] ${billing.state === 'past_due' ? 'border-warn-line!' : ''}`}
      role="region"
      aria-label="Your plan"
    >
      {billing.state === 'trial' ? <Trial billing={billing} /> : null}
      {billing.state === 'covered' ? <Covered billing={billing} source={source} /> : null}
      {billing.state === 'active' ? <Active billing={billing} /> : null}
      {billing.state === 'cancelling' ? <Cancelling billing={billing} /> : null}
      {billing.state === 'past_due' ? <PastDue billing={billing} /> : null}
      {billing.state === 'read_only' ? <ReadOnly billing={billing} /> : null}
    </Card>
  )
}

/** Back from the checkout, before the provider has told us: the plan changes by itself once it does. */
function Confirming() {
  return (
    <Card className="flex items-start gap-4 border-chip-selected-line! p-5" role="status" aria-label="Your plan">
      <svg aria-hidden="true" width="28" height="28" viewBox="0 0 28 28" className="mt-0.5 flex-none animate-spin">
        <circle cx="14" cy="14" r="11" fill="none" stroke="var(--line)" strokeWidth="3" />
        <path d="M14 3a11 11 0 0 1 11 11" fill="none" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <div className="flex min-w-0 flex-col gap-2">
        <h2 className="m-0 text-lg font-semibold">Confirming your payment</h2>
        <p className="m-0 text-muted">
          This usually takes a few seconds. You can leave this page: your plan changes to Pro by itself once the payment
          provider confirms.
        </p>
        <p className="m-0 text-[13px] text-muted">
          Nothing after a minute? Open Manage billing to check the payment, or write to hello@roundturn.app.
        </p>
      </div>
    </Card>
  )
}

/** The states in which the page offers a promo code (not to someone paying already). */
const TAKES_CODES: ReadonlySet<Billing['state']> = new Set(['trial', 'covered', 'read_only'])

/**
 * The Plan & billing page while billing is on: one card for where the person stands (trial, Pro from a code, paying,
 * cancelling, a failed payment, read-only), with what they can do about it. `checkout`: they are back from the payment page.
 */
export function BillingPlan({
  billing,
  source,
  promoMissed,
  promoSaved = false,
  checkout,
}: {
  billing: Billing
  source: PlanSource
  promoMissed: boolean
  promoSaved?: boolean
  checkout: CheckoutReturn
}) {
  const paidUp = isPaidUp(billing)
  const waiting = checkout === 'waiting' && !paidUp
  return (
    <>
      <SectionHeader title="Plan & billing">What you have, and what you pay.</SectionHeader>
      {promoMissed ? <PromoNotApplied /> : null}
      {promoSaved ? <PromoSaved /> : null}
      {checkout !== null && paidUp ? (
        <p role="status" className="m-0 rounded-[10px] border border-line bg-raised px-4 py-3 text-[13px] text-pos">
          <strong>Payment confirmed.</strong> Thank you: Roundturn Pro is active.
        </p>
      ) : null}
      {checkout === 'late' && !paidUp ? (
        <p role="status" className="m-0 rounded-[10px] border border-warn-line bg-raised px-4 py-3 text-[13px]">
          <strong>Your payment is not confirmed yet.</strong> If you paid, your plan changes to Pro by itself once the
          payment provider confirms. Still nothing in a few minutes? Write to hello@roundturn.app.
        </p>
      ) : null}
      {waiting ? <Confirming /> : <PlanCard billing={billing} source={source} />}
      {!waiting && TAKES_CODES.has(billing.state) ? <RedeemCode /> : null}
      {waiting ? null : (
        <p className="m-0 text-[13px] text-muted">
          Payment happens on our payment provider’s page; Roundturn never sees your card. See the{' '}
          <a href={REFUNDS_URL}>billing and refund policy</a>.
        </p>
      )}
    </>
  )
}
