import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, unwrap } from './api/client'
import type { components } from './api/schema'
import { go } from './auth'

export type Billing = components['schemas']['BillingOut']
export type BillingState = Billing['state']

const STATES: readonly BillingState[] = ['trial', 'covered', 'active', 'cancelling', 'past_due', 'read_only']

/** The answer, if it is one: anything else (a server that does not know the route) counts as billing being off. */
export function billingOf(body: unknown): Billing | null {
  if (typeof body !== 'object' || body === null) return null
  const state = (body as { state?: unknown }).state
  return typeof state === 'string' && (STATES as readonly string[]).includes(state) ? (body as Billing) : null
}

/**
 * Where the person's billing stands, or null while billing is switched off (the server answers 404, and the Plan page
 * shows what it always showed). Read once a minute at most; the actions below read it again when they change it.
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
    refetchInterval: options.poll ? 2_000 : false,
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

/** A fresh link to the provider's billing page, opened in a new tab (only an https address is followed). */
export function useManageBilling() {
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/billing/portal')),
    onSuccess: ({ url }) => {
      if (url.startsWith('https://')) window.open(url, '_blank', 'noopener,noreferrer')
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
