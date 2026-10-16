import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, unwrap } from './api/client'
import type { components } from './api/schema'
import { go } from './auth'
import { daysLeft, pluralize } from './format'

export type Billing = components['schemas']['BillingOut']
export type BillingState = Billing['state']

const STATES: readonly BillingState[] = ['trial', 'covered', 'active', 'cancelling', 'past_due', 'read_only']

/**
 * A subscription with nothing owed: what a finished checkout (a new subscription, a failed payment paid, a new card)
 * turns into once the provider confirms it. Not `past_due`, which someone paying a failed renewal comes back from.
 */
export function isPaidUp(billing: Billing | null | undefined): boolean {
  return billing?.state === 'active' || billing?.state === 'cancelling'
}

/** The answer, if it is one: anything else (a server that does not know the route) counts as billing being off. */
export function billingOf(body: unknown): Billing | null {
  if (typeof body !== 'object' || body === null) return null
  const state = (body as { state?: unknown }).state
  return typeof state === 'string' && (STATES as readonly string[]).includes(state) ? (body as Billing) : null
}

/**
 * Where the person's billing stands, or null while billing is switched off (the server answers 404, and the Plan page
 * shows what it always showed). Read once a minute at most; the actions below read it again when they change it.
 * `poll`: back from the checkout, read it every 2 seconds until the subscription shows paid up.
 */
export function useBilling(options: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: ['billing'],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/v1/billing')
      if (response.status === 404) return null
      if (data === undefined) throw new ApiError(response.status)
      return billingOf(data)
    },
    staleTime: 60_000,
    retry: false,
    refetchInterval: options.poll ? (query) => (isPaidUp(query.state.data) ? false : 2_000) : false,
  })
}

function useBillingChange<T>(call: () => Promise<T>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: call,
    onSuccess: (answer) => {
      const state = billingOf(answer)
      if (state) queryClient.setQueryData(['billing'], state)
      return Promise.all(['session', 'billing'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
    },
  })
}

/** Open a checkout and go to the payment page. The card form is the provider's; this site never shows one. */
export function useSubscribe() {
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/billing/checkout')),
    onSuccess: (checkout) => {
      if (checkout.pay_url.startsWith('/') && !checkout.pay_url.startsWith('//')) go.to(checkout.pay_url)
    },
  })
}

export function useCancelSubscription() {
  return useBillingChange(() => unwrap(api.POST('/api/v1/billing/cancel')))
}

export function useResumeSubscription() {
  return useBillingChange(() => unwrap(api.POST('/api/v1/billing/resume')))
}

/**
 * A fresh link to the provider's billing page, opened in a new tab (only an https address is followed). A browser that
 * blocks a tab opened after a request gets the page in this tab instead. The new tab cannot reach back to this one.
 */
export function useManageBilling() {
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/billing/portal')),
    onSuccess: ({ url }) => {
      if (!url.startsWith('https://')) return
      const tab = window.open(url, '_blank')
      if (tab) tab.opener = null
      else go.to(url)
    },
  })
}

/** "Oct 21" (and the year when it is not this year), in UTC like every date the server sends. */
export function billingDay(iso: string | null, now: Date = new Date()): string {
  if (!iso) return ''
  const date = new Date(iso)
  const sameYear = date.getUTCFullYear() === now.getUTCFullYear()
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
    timeZone: 'UTC',
  }).format(date)
}

/** "USD 15" and "a month": the price as the page shows it, cents only when there are some. */
export function priceOf(billing: Billing): { amount: string; per: string } {
  const amount = billing.price.endsWith('.00') ? billing.price.slice(0, -3) : billing.price
  return { amount: `${billing.currency} ${amount}`, per: `a ${billing.interval}` }
}

export type TagTone = 'quiet' | 'warn' | 'bad'

/** The plan tag in the header while billing is on: what the person has, amber when it needs them soon, red when ended. */
export function planTag(billing: Billing, now: Date = new Date()): { text: string; tone: TagTone } {
  const left = daysLeft(billing.ends_at, now)
  switch (billing.state) {
    case 'trial':
      return left === null
        ? { text: 'Trial', tone: 'warn' }
        : { text: `Trial · ${pluralize(left, 'day')} left`, tone: left <= 3 ? 'warn' : 'quiet' }
    case 'covered':
      return { text: left === null ? 'Pro' : `Pro · ${pluralize(left, 'day')} left`, tone: 'quiet' }
    case 'active':
      return { text: 'Pro', tone: 'quiet' }
    case 'cancelling':
      return { text: `Pro · ends ${billingDay(billing.ends_at, now)}`, tone: 'warn' }
    case 'past_due':
      return { text: 'Payment failed', tone: 'warn' }
    case 'read_only':
      return { text: 'Read-only', tone: 'bad' }
  }
}

export type BannerKind = 'read_only' | 'payment' | 'trial'

/** The one banner under the header, if any: read-only first, then a failed payment, then the trial's last 3 days. */
export function bannerOf(billing: Billing | null | undefined, now: Date = new Date()): BannerKind | null {
  if (!billing) return null
  if (billing.state === 'read_only') return 'read_only'
  if (billing.state === 'past_due') return 'payment'
  if (billing.state === 'trial') {
    const left = daysLeft(billing.ends_at, now)
    if (left !== null && left <= 3) return 'trial'
  }
  return null
}

const HIDDEN_KEY = 'tj-banner-hidden-'

/** "2026-10-16" in this browser's own time, so a banner closed for the day comes back the next morning. */
function localDay(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** Whether this banner was closed earlier today in this browser. The read-only banner cannot be closed. */
export function bannerHiddenToday(kind: BannerKind, now: Date = new Date()): boolean {
  if (kind === 'read_only') return false
  try {
    return window.localStorage.getItem(HIDDEN_KEY + kind) === localDay(now)
  } catch {
    return false // storage can be blocked; the banner then simply shows
  }
}

export function hideBannerToday(kind: BannerKind, now: Date = new Date()): void {
  try {
    window.localStorage.setItem(HIDDEN_KEY + kind, localDay(now))
  } catch {
    // see bannerHiddenToday
  }
}
