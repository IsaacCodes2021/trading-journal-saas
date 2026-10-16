import { afterEach, describe, expect, it, vi } from 'vitest'
import { billingBody } from '../test-utils'
import {
  bannerHiddenToday,
  bannerOf,
  billingDay,
  billingOf,
  hideBannerToday,
  isPaidUp,
  planTag,
  priceOf,
  type Billing,
} from './billing'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

const NOW = new Date('2026-10-16T09:00:00Z')
const at = (days: number) => new Date(NOW.getTime() + days * 86_400_000).toISOString()
const billing = (changes: Partial<Billing> = {}) => billingBody(changes) as Billing

describe('billingOf', () => {
  it('takes an answer with a state it knows', () => {
    for (const state of ['trial', 'covered', 'active', 'cancelling', 'past_due', 'read_only'] as const) {
      expect(billingOf(billing({ state }))?.state).toBe(state)
    }
  })

  it('counts anything else as billing being off', () => {
    expect(billingOf(null)).toBeNull()
    expect(billingOf('trial')).toBeNull()
    expect(billingOf({})).toBeNull()
    expect(billingOf({ state: 'paused' })).toBeNull()
    expect(billingOf({ access: { state: 'active' } })).toBeNull() // a session answer, from a server without the route
  })
})

describe('isPaidUp', () => {
  it('is true only with a subscription that owes nothing', () => {
    expect(isPaidUp(billing({ state: 'active' }))).toBe(true)
    expect(isPaidUp(billing({ state: 'cancelling' }))).toBe(true)
    expect(isPaidUp(billing({ state: 'past_due' }))).toBe(false) // what someone paying a failed renewal comes back from
    expect(isPaidUp(billing({ state: 'trial' }))).toBe(false)
    expect(isPaidUp(billing({ state: 'covered' }))).toBe(false)
    expect(isPaidUp(billing({ state: 'read_only' }))).toBe(false)
    expect(isPaidUp(null)).toBe(false)
    expect(isPaidUp(undefined)).toBe(false)
  })
})

describe('priceOf', () => {
  it('drops cents that are zero, and keeps ones that are not', () => {
    expect(priceOf(billing())).toEqual({ amount: 'USD 15', per: 'a month' })
    expect(priceOf(billing({ price: '14.50' }))).toEqual({ amount: 'USD 14.50', per: 'a month' })
    expect(priceOf(billing({ price: '100.00', currency: 'EUR' }))).toEqual({ amount: 'EUR 100', per: 'a month' })
  })
})

describe('billingDay', () => {
  it('says the day in UTC, with the year only when it is not this year', () => {
    expect(billingDay('2026-10-21T23:30:00Z', NOW)).toBe('Oct 21')
    expect(billingDay('2027-01-02T00:30:00Z', NOW)).toBe('Jan 2, 2027')
    expect(billingDay(null, NOW)).toBe('')
  })
})

describe('planTag', () => {
  it('counts the trial down, quiet until its last 3 days and amber in them', () => {
    expect(planTag(billing({ state: 'trial', ends_at: at(4.5) }), NOW)).toEqual({
      text: 'Trial · 5 days left',
      tone: 'quiet',
    })
    expect(planTag(billing({ state: 'trial', ends_at: at(3.01) }), NOW)).toEqual({
      text: 'Trial · 4 days left',
      tone: 'quiet',
    })
    expect(planTag(billing({ state: 'trial', ends_at: at(3) }), NOW)).toEqual({
      text: 'Trial · 3 days left',
      tone: 'warn',
    })
    expect(planTag(billing({ state: 'trial', ends_at: at(0.1) }), NOW)).toEqual({
      text: 'Trial · 1 day left',
      tone: 'warn',
    })
  })

  it('says Pro, with the days left from a code or the team, and without them when there is no end', () => {
    expect(planTag(billing({ state: 'covered', ends_at: at(25.2) }), NOW)).toEqual({
      text: 'Pro · 26 days left',
      tone: 'quiet',
    })
    expect(planTag(billing({ state: 'covered', ends_at: null }), NOW)).toEqual({ text: 'Pro', tone: 'quiet' })
  })

  it('says Pro while paying, and when it ends once cancelled', () => {
    expect(planTag(billing({ state: 'active', ends_at: null, renews_at: at(29) }), NOW)).toEqual({
      text: 'Pro',
      tone: 'quiet',
    })
    expect(planTag(billing({ state: 'cancelling', ends_at: '2026-11-14T09:00:00Z' }), NOW)).toEqual({
      text: 'Pro · ends Nov 14',
      tone: 'warn',
    })
  })

  it('says a payment failed, and read-only in red', () => {
    expect(planTag(billing({ state: 'past_due', ends_at: at(5) }), NOW)).toEqual({
      text: 'Payment failed',
      tone: 'warn',
    })
    expect(planTag(billing({ state: 'read_only', ends_at: at(-3) }), NOW)).toEqual({ text: 'Read-only', tone: 'bad' })
  })
})

describe('bannerOf', () => {
  it('shows read-only, a failed payment, and the trial only in its last 3 days', () => {
    expect(bannerOf(billing({ state: 'read_only' }), NOW)).toBe('read_only')
    expect(bannerOf(billing({ state: 'past_due', ends_at: at(6) }), NOW)).toBe('payment')
    expect(bannerOf(billing({ state: 'trial', ends_at: at(3) }), NOW)).toBe('trial')
    expect(bannerOf(billing({ state: 'trial', ends_at: at(3.01) }), NOW)).toBeNull()
    expect(bannerOf(billing({ state: 'trial', ends_at: at(-0.01) }), NOW)).toBeNull() // the server says read-only next
  })

  it('shows nothing for Pro, paying, cancelling, or billing off', () => {
    expect(bannerOf(billing({ state: 'covered', ends_at: at(1) }), NOW)).toBeNull()
    expect(bannerOf(billing({ state: 'active' }), NOW)).toBeNull()
    expect(bannerOf(billing({ state: 'cancelling', ends_at: at(1) }), NOW)).toBeNull()
    expect(bannerOf(null, NOW)).toBeNull()
  })
})

describe('a banner closed for the day', () => {
  it('stays closed for the rest of the day in this browser, and comes back the next day', () => {
    const evening = new Date(2026, 9, 16, 21, 0)
    expect(bannerHiddenToday('trial', evening)).toBe(false)
    hideBannerToday('trial', new Date(2026, 9, 16, 8, 0))
    expect(bannerHiddenToday('trial', evening)).toBe(true)
    expect(bannerHiddenToday('payment', evening)).toBe(false) // each banner on its own
    expect(bannerHiddenToday('trial', new Date(2026, 9, 17, 0, 1))).toBe(false)
  })

  it('never hides the read-only banner', () => {
    hideBannerToday('read_only', NOW)
    expect(bannerHiddenToday('read_only', NOW)).toBe(false)
  })

  it('simply shows the banner when the browser keeps no storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => hideBannerToday('trial', NOW)).not.toThrow()
    expect(bannerHiddenToday('trial', NOW)).toBe(false)
  })
})
