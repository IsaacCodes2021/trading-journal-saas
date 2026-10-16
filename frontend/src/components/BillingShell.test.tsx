import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { billingBody, openApp, stubApi, type ApiOptions } from '../test-utils'

// Only the clock is faked, so "5 days left" does not depend on the day the tests run; timers and promises stay real.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-16T09:00:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
})

async function open(opts: ApiOptions = {}) {
  stubApi(opts)
  openApp('/')
  return screen.findByRole('navigation', { name: 'Main' })
}

const tag = (name: string | RegExp) => screen.findByRole('link', { name })
const banner = () => screen.queryByText(/Your free trial ends|Your last payment|Your journal is read-only/)

const ACTIVE = billingBody({ state: 'active', ends_at: null, renews_at: '2026-11-14T09:00:00Z', can_subscribe: false })

describe('the plan tag', () => {
  it('reads as before while billing is off, and leads to Plan & billing', async () => {
    await open()
    expect(await tag('Free')).toHaveAttribute('href', '/settings/plan')
    expect(banner()).toBeNull()
  })

  it('reads as before when billing could not be read', async () => {
    stubApi()
    const normal = globalThis.fetch
    vi.stubGlobal(
      'fetch',
      vi.fn((request: Request) =>
        new URL(request.url).pathname === '/api/v1/billing'
          ? Promise.resolve(new Response('{}', { status: 500 }))
          : normal(request),
      ),
    )
    openApp('/')
    expect(await tag('Free')).toBeInTheDocument()
  })

  it('says where billing stands in each state, and always leads to Plan & billing', async () => {
    const cases: [object, string][] = [
      [billingBody({ ends_at: '2026-10-20T21:00:00Z' }), 'Trial · 5 days left'],
      [billingBody({ ends_at: '2026-10-18T09:00:00Z' }), 'Trial · 2 days left'],
      [billingBody({ state: 'covered', ends_at: '2026-11-11T09:00:00Z' }), 'Pro · 26 days left'],
      [billingBody({ state: 'covered', ends_at: null, can_subscribe: false }), 'Pro'],
      [ACTIVE, 'Pro'],
      [{ ...ACTIVE, state: 'cancelling', ends_at: '2026-11-14T09:00:00Z', renews_at: null }, 'Pro · ends Nov 14'],
      [{ ...ACTIVE, state: 'past_due', ends_at: '2026-10-21T09:00:00Z', renews_at: null }, 'Payment failed'],
      [billingBody({ state: 'read_only', ends_at: '2026-10-10T09:00:00Z', reason: 'lapsed' }), 'Read-only'],
    ]
    for (const [billing, text] of cases) {
      await open({ billing, plan: { effective: 'pro', source: 'grant', expires_at: '2027-01-01T00:00:00Z' } })
      expect(await tag(text)).toHaveAttribute('href', '/settings/plan')
      cleanup()
    }
  })
})

describe('the billing banner', () => {
  it('shows nothing before the trial’s last 3 days, or while Pro', async () => {
    await open({ billing: billingBody({ ends_at: '2026-10-20T09:00:00Z' }) })
    await tag('Trial · 4 days left')
    expect(banner()).toBeNull()
    cleanup()
    await open({ billing: ACTIVE })
    await tag('Pro')
    expect(banner()).toBeNull()
  })

  it('counts the trial’s last days down, leads to Subscribe, and can be closed until tomorrow', async () => {
    await open({ billing: billingBody({ ends_at: '2026-10-18T09:00:00Z' }) })
    const user = userEvent.setup()
    const shown = (await screen.findByText(/Your free trial ends in 2 days/)).closest('[role="status"]') as HTMLElement
    expect(shown).toHaveTextContent('Subscribe to keep importing and editing.')
    expect(within(shown).getByRole('link', { name: 'Subscribe' })).toHaveAttribute('href', '/settings/plan')
    await user.click(within(shown).getByRole('button', { name: 'Hide until tomorrow' }))
    expect(banner()).toBeNull()

    cleanup() // later the same day, in the same browser
    await open({ billing: billingBody({ ends_at: '2026-10-18T09:00:00Z' }) })
    await tag('Trial · 2 days left')
    expect(banner()).toBeNull()

    cleanup() // the next day
    vi.setSystemTime(new Date('2026-10-17T09:00:00Z'))
    await open({ billing: billingBody({ ends_at: '2026-10-18T09:00:00Z' }) })
    expect(await screen.findByText(/Your free trial ends in 1 day\./)).toBeInTheDocument()
  })

  it('says a payment failed and until when access lasts, with a way to the card', async () => {
    await open({ billing: { ...ACTIVE, state: 'past_due', ends_at: '2026-10-21T09:00:00Z', renews_at: null } })
    const shown = (await screen.findByText(/Your last payment did not go through/)).closest('[role="status"]')
    expect(shown).toHaveTextContent('Full access continues until Oct 21.')
    expect(within(shown as HTMLElement).getByRole('link', { name: 'Update payment method' })).toHaveAttribute(
      'href',
      '/settings/plan',
    )
    expect(within(shown as HTMLElement).getByRole('button', { name: 'Hide until tomorrow' })).toBeInTheDocument()
  })

  it('says the journal is read-only, and cannot be closed', async () => {
    await open({ billing: billingBody({ state: 'read_only', ends_at: '2026-10-10T09:00:00Z', reason: 'expired' }) })
    const shown = (await screen.findByText(/Your journal is read-only/)).closest('[role="status"]') as HTMLElement
    expect(shown).toHaveTextContent('You can look at everything and download it; importing and editing are paused.')
    expect(within(shown).getByRole('link', { name: 'Subscribe' })).toHaveAttribute('href', '/settings/plan')
    expect(within(shown).queryByRole('button')).not.toBeInTheDocument()
  })
})
