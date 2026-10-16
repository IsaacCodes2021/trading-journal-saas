import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { go } from '../../lib/auth'
import { billingBody, openApp, stubApi, type ApiOptions } from '../../test-utils'

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

const TRIAL = billingBody({ ends_at: '2026-10-21T09:00:00Z', billing_starts_at: '2026-10-21T09:00:00Z' })
const COVERED = billingBody({
  state: 'covered',
  ends_at: '2026-11-11T09:00:00Z',
  billing_starts_at: '2026-11-11T09:00:00Z',
})
const ACTIVE = billingBody({
  state: 'active',
  ends_at: null,
  renews_at: '2026-11-14T09:00:00Z',
  can_subscribe: false,
  billing_starts_at: null,
  can_manage: true,
})
const CANCELLING = { ...ACTIVE, state: 'cancelling', ends_at: '2026-11-14T09:00:00Z', renews_at: null }
const PAST_DUE = { ...ACTIVE, state: 'past_due', ends_at: '2026-10-21T09:00:00Z', renews_at: null }
const READ_ONLY = billingBody({
  state: 'read_only',
  ends_at: '2026-10-10T09:00:00Z',
  billing_starts_at: null,
  reason: 'lapsed',
  can_manage: true,
})

async function open(opts: ApiOptions = {}, search = '') {
  stubApi(opts)
  openApp(`/settings/plan${search}`)
  await screen.findByRole('heading', { name: 'Plan & billing' })
  return userEvent.setup()
}

const plan = () => screen.findByRole('region', { name: 'Your plan' })

/** The requests a page made to one billing path: the method and the CSRF token. */
function sent(path: string) {
  return (vi.mocked(globalThis.fetch).mock.calls as unknown as [Request][])
    .map(([request]) => request)
    .filter((request) => new URL(request.url).pathname === path)
    .map((request) => ({ method: request.method, csrf: request.headers.get('X-CSRF-Token') }))
}

describe('billing switched off', () => {
  it('shows the page as it always was, with no price and nothing to subscribe to', async () => {
    await open()
    expect(await plan()).toHaveTextContent('The free plan.')
    expect(screen.getByRole('region', { name: 'What your plan includes' })).toBeInTheDocument()
    expect(screen.getByText(/Paid plans open after the beta/)).toBeInTheDocument()
    expect(screen.queryByText(/USD/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Subscribe' })).not.toBeInTheDocument()
  })

  it('says so when billing could not be read, rather than that paid plans are not open', async () => {
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
    openApp('/settings/plan')
    expect(await screen.findByRole('alert')).toHaveTextContent('Your billing details could not be loaded.')
    expect(screen.queryByText(/Paid plans open after the beta/)).not.toBeInTheDocument()
  })
})

describe('on the trial', () => {
  it('says how long is left, the price, and when billing would start', async () => {
    await open({ billing: TRIAL })
    const card = await plan()
    expect(within(card).getByRole('heading', { name: 'Free trial' })).toBeInTheDocument()
    expect(card).toHaveTextContent('5 days left')
    expect(card).toHaveTextContent('Full access to everything until Oct 21. No card needed.')
    expect(card).toHaveTextContent('Roundturn Pro')
    expect(card).toHaveTextContent('USD 15 a month')
    expect(card).toHaveTextContent('Billing starts on Oct 21, when your trial ends.')
    expect(card).toHaveTextContent('Taxes, where they apply, are added at checkout.')
    expect(card).toHaveTextContent('Cancel any time on this page. Payments are not refunded.')
    expect(screen.getByText('What you have, and what you pay.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'What your plan includes' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Redeem a promo code' })).toBeInTheDocument()
  })

  it('links the billing and refund policy, and says the card is never seen here', async () => {
    await open({ billing: TRIAL })
    expect(screen.getByRole('link', { name: 'billing and refund policy' })).toHaveAttribute(
      'href',
      'https://roundturn.app/refunds/',
    )
    expect(screen.getByText(/Roundturn never sees your card/)).toBeInTheDocument()
  })

  it('subscribes with the CSRF token and goes to the payment page the server names', async () => {
    const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
    const user = await open({ billing: TRIAL })
    await user.click(within(await plan()).getByRole('button', { name: 'Subscribe' }))
    await waitFor(() => expect(leave).toHaveBeenCalledWith('/billing/pay?_ptxn=txn_test_1'))
    expect(sent('/api/v1/billing/checkout')).toEqual([{ method: 'POST', csrf: 'tok' }])
    expect(within(await plan()).getByRole('button', { name: 'Opening checkout…' })).toBeDisabled() // no second checkout
  })

  it('goes nowhere when the payment page is not on this site', async () => {
    const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
    for (const pay_url of ['https://pay.example.test/x', '//pay.example.test/x']) {
      const user = await open({ billing: TRIAL, billingAnswers: { checkout: { status: 200, body: { pay_url } } } })
      await user.click(within(await plan()).getByRole('button', { name: 'Subscribe' }))
      await waitFor(() => expect(sent('/api/v1/billing/checkout')).toHaveLength(1))
      cleanup()
    }
    expect(leave).not.toHaveBeenCalled()
  })

  it('shows the server’s sentence when no checkout could be opened, and can be tried again', async () => {
    const detail =
      'The payment provider did not answer. Nothing was charged and nothing changed; try again in a minute.'
    const user = await open({
      billing: TRIAL,
      billingAnswers: { checkout: { status: 503, body: { title: 'x', status: 503, detail, errors: null } } },
    })
    const card = await plan()
    await user.click(within(card).getByRole('button', { name: 'Subscribe' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent(detail)
    expect(within(card).getByRole('button', { name: 'Subscribe' })).toBeEnabled()
  })

  it('turns the days left amber in the trial’s last 3 days', async () => {
    await open({ billing: { ...TRIAL, ends_at: '2026-10-18T09:00:00Z' } })
    expect(within(await plan()).getByText('2 days left')).toHaveClass('text-warn')
  })
})

describe('Pro from an invitation, a code or the team', () => {
  it('says where it comes from and until when, and offers to keep Pro after it', async () => {
    await open({ billing: COVERED, plan: { effective: 'pro', source: 'grant', expires_at: '2026-11-11T09:00:00Z' } })
    const card = await plan()
    expect(within(card).getByRole('heading', { name: 'Pro' })).toBeInTheDocument()
    expect(card).toHaveTextContent('26 days left')
    expect(card).toHaveTextContent('From an invitation or a code, until Nov 11.')
    expect(card).toHaveTextContent('Keep Pro after Nov 11')
    expect(card).toHaveTextContent(
      'Billing starts on Nov 11, when your free days end, so you are never charged for days you already have.',
    )
    expect(within(card).getByRole('button', { name: 'Subscribe' })).toBeInTheDocument()
  })

  it('says the team set it, and offers nothing to pay for when it has no end', async () => {
    await open({
      billing: { ...COVERED, ends_at: null, billing_starts_at: null, can_subscribe: false },
      plan: { effective: 'pro', source: 'override', expires_at: null },
    })
    const card = await plan()
    expect(card).toHaveTextContent('Set for you by the team.')
    expect(card).not.toHaveTextContent('days left')
    expect(within(card).queryByRole('button', { name: 'Subscribe' })).not.toBeInTheDocument()
  })
})

describe('paying', () => {
  it('says the price, when it renews, and offers the provider’s page and Cancel, but no promo code', async () => {
    await open({ billing: ACTIVE })
    const card = await plan()
    expect(within(card).getByRole('heading', { name: 'Pro' })).toBeInTheDocument()
    expect(card).toHaveTextContent('USD 15 a month')
    expect(card).toHaveTextContent('Renews on Nov 14.')
    expect(card).toHaveTextContent('Cancel any time. You keep Pro until Nov 14, and nothing is deleted.')
    expect(within(card).getByRole('button', { name: 'Manage billing' })).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Cancel subscription' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Redeem a promo code' })).not.toBeInTheDocument()
  })

  it('says when the first payment is taken while free days remain', async () => {
    await open({ billing: { ...ACTIVE, first_charge_pending: true, renews_at: '2026-11-02T09:00:00Z' } })
    expect(await plan()).toHaveTextContent('Your first payment is on Nov 2, when your free days end.')
  })

  it('has no Manage billing until the provider has a customer', async () => {
    await open({ billing: { ...ACTIVE, can_manage: false } })
    expect(within(await plan()).queryByRole('button', { name: 'Manage billing' })).not.toBeInTheDocument()
  })

  it('opens the provider’s page in a new tab that cannot reach back', async () => {
    const tab = { opener: 'this window' }
    const opened = vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    const user = await open({ billing: ACTIVE })
    await user.click(within(await plan()).getByRole('button', { name: 'Manage billing' }))
    await waitFor(() => expect(opened).toHaveBeenCalledWith('https://billing.example.test/portal/ctm_1', '_blank'))
    expect(tab.opener).toBeNull()
    expect(sent('/api/v1/billing/portal')).toEqual([{ method: 'POST', csrf: 'tok' }])
  })

  it('opens it in this tab when the browser blocks a new one', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null)
    const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
    const user = await open({ billing: ACTIVE })
    await user.click(within(await plan()).getByRole('button', { name: 'Manage billing' }))
    await waitFor(() => expect(leave).toHaveBeenCalledWith('https://billing.example.test/portal/ctm_1'))
  })

  it('follows no link that is not https', async () => {
    const opened = vi.spyOn(window, 'open').mockReturnValue(null)
    const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
    const user = await open({
      billing: ACTIVE,
      billingAnswers: { portal: { status: 200, body: { url: 'javascript:alert(1)' } } },
    })
    await user.click(within(await plan()).getByRole('button', { name: 'Manage billing' }))
    await waitFor(() => expect(sent('/api/v1/billing/portal')).toHaveLength(1))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(opened).not.toHaveBeenCalled()
    expect(leave).not.toHaveBeenCalled()
  })
})

describe('cancelling', () => {
  const dialog = () => screen.findByRole('dialog', { name: 'Cancel your subscription' })

  it('asks first, saying what stays and that nothing is refunded', async () => {
    const user = await open({ billing: ACTIVE })
    await user.click(within(await plan()).getByRole('button', { name: 'Cancel subscription' }))
    const ask = await dialog()
    expect(within(ask).getByRole('heading', { name: 'Cancel your subscription?' })).toBeInTheDocument()
    expect(ask).toHaveTextContent('You keep full access until Nov 14.')
    expect(ask).toHaveTextContent('Your subscription will not renew, and payments are not refunded.')
    expect(ask).toHaveTextContent('After Nov 14 your journal is read-only: everything stays, to look at and download.')
    expect(within(ask).getByRole('button', { name: 'Keep subscription' })).toHaveFocus() // the safe choice is the default
  })

  it('says nothing is charged when the first payment has not been taken', async () => {
    const user = await open({ billing: { ...ACTIVE, first_charge_pending: true } })
    await user.click(within(await plan()).getByRole('button', { name: 'Cancel subscription' }))
    expect(await dialog()).toHaveTextContent('Your subscription will not start, and nothing is charged.')
  })

  it('sends nothing when the person keeps the subscription', async () => {
    const user = await open({ billing: ACTIVE })
    await user.click(within(await plan()).getByRole('button', { name: 'Cancel subscription' }))
    await user.click(within(await dialog()).getByRole('button', { name: 'Keep subscription' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(sent('/api/v1/billing/cancel')).toEqual([])
  })

  it('cancels with the CSRF token, closes, and shows when it ends with a way to resume', async () => {
    const user = await open({ billing: ACTIVE })
    await user.click(within(await plan()).getByRole('button', { name: 'Cancel subscription' }))
    await user.click(within(await dialog()).getByRole('button', { name: 'Cancel subscription' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(sent('/api/v1/billing/cancel')).toEqual([{ method: 'POST', csrf: 'tok' }])
    const card = await plan()
    await waitFor(() => expect(card).toHaveTextContent('Ends Nov 14'))
    expect(within(card).getByRole('button', { name: 'Resume subscription' })).toBeInTheDocument()
  })

  it('stays open with the server’s sentence when the cancel is refused', async () => {
    const detail = 'There is no renewing subscription to cancel.'
    const user = await open({
      billing: ACTIVE,
      billingAnswers: { cancel: { status: 409, body: { title: 'x', status: 409, detail, errors: null } } },
    })
    await user.click(within(await plan()).getByRole('button', { name: 'Cancel subscription' }))
    const ask = await dialog()
    await user.click(within(ask).getByRole('button', { name: 'Cancel subscription' }))
    expect(await within(ask).findByRole('alert')).toHaveTextContent(detail)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

describe('cancelled, the month still running', () => {
  it('says when Pro ends, what happens after, and offers to resume', async () => {
    await open({ billing: CANCELLING })
    const card = await plan()
    expect(card).toHaveTextContent('Ends Nov 14')
    expect(card).toHaveTextContent('Your subscription will not renew. You keep full access until Nov 14.')
    expect(card).toHaveTextContent('Resume before Nov 14 and it renews as before, with nothing charged now.')
    expect(within(card).getByRole('button', { name: 'Manage billing' })).toBeInTheDocument()
  })

  it('resumes with the CSRF token and shows the renewal again', async () => {
    const user = await open({ billing: CANCELLING })
    await user.click(within(await plan()).getByRole('button', { name: 'Resume subscription' }))
    await waitFor(async () => expect(await plan()).toHaveTextContent('Renews on Nov 14.'))
    expect(sent('/api/v1/billing/resume')).toEqual([{ method: 'POST', csrf: 'tok' }])
  })
})

describe('a failed payment', () => {
  it('says what happened, how long access lasts, and offers to update the card', async () => {
    vi.spyOn(window, 'open').mockReturnValue({ opener: null } as unknown as Window)
    const user = await open({ billing: PAST_DUE })
    const card = await plan()
    expect(card).toHaveTextContent('Payment failed')
    expect(card).toHaveTextContent('Your last payment did not go through')
    expect(card).toHaveTextContent('You keep full access until Oct 21.')
    expect(card).toHaveTextContent('Nothing is deleted.')
    await user.click(within(card).getByRole('button', { name: 'Update payment method' }))
    await waitFor(() => expect(sent('/api/v1/billing/portal')).toHaveLength(1))
  })
})

describe('read-only', () => {
  it('says why and since when, that everything is still there, with the download and Subscribe', async () => {
    await open({ billing: READ_ONLY })
    const card = await plan()
    expect(within(card).getByRole('heading', { name: 'Read-only' })).toBeInTheDocument()
    expect(card).toHaveTextContent('Your subscription ended on Oct 10.')
    expect(card).toHaveTextContent('Your journal is all still here')
    expect(within(card).getByRole('link', { name: 'download your data' })).toHaveAttribute('href', '/api/v1/export')
    expect(card).toHaveTextContent('Billing starts when you subscribe and renews monthly.')
    expect(within(card).getByRole('button', { name: 'Subscribe' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Redeem a promo code' })).toBeInTheDocument()
  })

  it('says when free days or the team ended it, and nothing about why when the server does not say', async () => {
    await open({ billing: { ...READ_ONLY, reason: 'expired' } })
    expect(await plan()).toHaveTextContent('Your free days ended on Oct 10.')
    cleanup()
    await open({ billing: { ...READ_ONLY, reason: 'revoked' } })
    expect(await plan()).toHaveTextContent('Your access was ended on Oct 10.')
    cleanup()
    await open({ billing: { ...READ_ONLY, reason: null, ends_at: null } })
    expect(await plan()).toHaveTextContent(/^Read-onlyYour journal is all still here/)
  })
})

describe('back from the checkout', () => {
  it('waits for the provider, then says the payment is confirmed and reads the plan again', async () => {
    await open({ billing: [TRIAL, ACTIVE] }, '?checkout=done')
    const waiting = await screen.findByRole('status', { name: 'Your plan' })
    expect(waiting).toHaveTextContent('Confirming your payment')
    expect(waiting).toHaveTextContent('You can leave this page')
    expect(screen.queryByRole('region', { name: 'Redeem a promo code' })).not.toBeInTheDocument()
    const sessionsBefore = sent('/api/v1/session').length
    expect(await screen.findByText('Payment confirmed.', {}, { timeout: 5000 })).toBeInTheDocument()
    expect(await plan()).toHaveTextContent('Renews on Nov 14.')
    await waitFor(() => expect(sent('/api/v1/session').length).toBeGreaterThan(sessionsBefore))
  })

  it('says the payment is not confirmed yet after a minute without word, and stops asking', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    vi.setSystemTime(new Date('2026-10-16T09:00:00Z'))
    stubApi({ billing: TRIAL })
    openApp('/settings/plan', { checkout: 'done' })
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(screen.getByRole('status', { name: 'Your plan' })).toHaveTextContent('Confirming your payment')
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(screen.getByText('Your payment is not confirmed yet.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Your plan' })).toHaveTextContent('Free trial')
    const reads = sent('/api/v1/billing').length
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(sent('/api/v1/billing')).toHaveLength(reads)
  })
})

describe('a discount code', () => {
  const DISCOUNT = { percent_off: 20, duration: 'repeating', duration_months: 3, words: '20% off your first 3 months' }

  it('says a saved code is applied at checkout, beside Subscribe', async () => {
    await open({ billing: { ...TRIAL, discount: DISCOUNT } })
    expect(await plan()).toHaveTextContent(
      'Your discount code is saved: 20% off your first 3 months, applied at checkout.',
    )
  })

  it('says nothing of a discount when none is saved', async () => {
    await open({ billing: TRIAL })
    expect(await plan()).not.toHaveTextContent('discount code')
  })

  it('tells someone who typed one in the box that it is saved for when they subscribe', async () => {
    const user = await open({
      billing: TRIAL,
      redeem: { status: 200, body: { effective: 'pro', source: 'grant', expires_at: null, discount: DISCOUNT } },
    })
    const box = screen.getByRole('region', { name: 'Redeem a promo code' })
    await user.type(within(box).getByLabelText('Promo code'), 'LAUNCH20')
    await user.click(within(box).getByRole('button', { name: 'Redeem' }))
    expect(await within(box).findByRole('status')).toHaveTextContent(
      'Saved: 20% off your first 3 months. It is applied when you subscribe.',
    )
  })

  it('says once, on the way in from sign-up, that the code it was made with is saved', async () => {
    await open({ billing: TRIAL }, '?promo=saved')
    expect(screen.getByText(/your discount code is saved/)).toBeInTheDocument()
  })
})
