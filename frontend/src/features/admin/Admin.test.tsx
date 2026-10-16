import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { go } from '../../lib/auth'
import { openApp } from '../../test-utils'
import { SELF, changes, detail, stubAdmin, user, type AdminStubOptions, type Call } from './admin-stub'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const DANA = user('11111111-1111-4111-8111-111111111111', 'dana.k@example.test', {
  plan: { effective: 'pro', source: 'override', expires_at: '2026-12-31T23:59:59Z' },
})
const LEO = user('22222222-2222-4222-8222-222222222222', 'leo.w@example.test', {
  status: 'suspended',
  usage: { accounts: 1, trades: 38, screenshots: 0, storage_bytes: 0, last_import_at: null },
})
const SAM = user('33333333-3333-4333-8333-333333333333', 'sam.r@example.test', {
  status: 'pending_deletion',
  deletion_scheduled_at: '2026-10-11T08:00:00Z',
  last_login_at: null,
})
const PRIYA = user('44444444-4444-4444-8444-444444444444', 'priya.n@example.test', {
  plan: { effective: 'pro', source: 'grant', expires_at: '2026-11-30T00:00:00Z' },
})
const OPERATOR = user('55555555-5555-4555-8555-555555555555', 'other.op@example.test', { is_admin: true })
const ME = user(SELF, 'me@example.test') // not marked as an operator here: only the id says it is the person themself
const ALL = [DANA, LEO, SAM, PRIYA, OPERATOR]

async function open(options: AdminStubOptions = {}, search: Record<string, string | number> = {}) {
  const calls = stubAdmin({ users: ALL, ...options })
  const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
  const app = openApp('/admin', search)
  return { ...app, calls, leave, user: userEvent.setup() }
}

const listed = async () => screen.findByRole('region', { name: 'Accounts' })
const panel = () => screen.findByRole('complementary', { name: 'Selected account' })
const requests = (calls: Call[], path: string) => calls.filter((c) => c.path === path)

describe('the operator gate', () => {
  it('turns away someone who is not an operator, and asks the server for nothing', async () => {
    const { calls } = await open({ operator: false })
    expect(await screen.findByRole('region', { name: 'Operators only' })).toHaveTextContent(
      'This area is for operators.',
    )
    expect(calls).toEqual([])
    expect(screen.queryByText('Not verified')).not.toBeInTheDocument()
  })

  it('asks an operator without a fresh second factor to confirm it, and says nothing else', async () => {
    const { calls, leave, user: u } = await open({ mfaFresh: false })
    const gate = await screen.findByRole('region', { name: 'Confirm it is you' })
    expect(screen.getByText('Not verified')).toBeInTheDocument()
    expect(calls).toEqual([])
    await u.click(within(gate).getByRole('button', { name: 'Verify with two-step verification' }))
    expect(leave).toHaveBeenCalledExactlyOnceWith('/auth/login?stepup=1&next=%2Fadmin')
  })

  it('shows an operator with a fresh second factor the list, under a header of its own', async () => {
    await open()
    await listed()
    expect(screen.getByText('Operator')).toBeInTheDocument()
    expect(screen.getByText('Two-step verification current')).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Operator' })
    expect(within(nav).getByRole('link', { name: 'Users' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Back to the app' })).toHaveAttribute('href', '/')
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument() // not the journal's header
  })

  it('says what operator views never show', async () => {
    await open()
    await listed()
    expect(screen.getByRole('note')).toHaveTextContent('Trades, notes and screenshots are never shown')
  })
})

describe('the way in', () => {
  const menu = async (operator: boolean) => {
    stubAdmin({ operator })
    openApp('/settings/preferences')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('button', { name: 'Account menu' }))
  }

  it('gives an operator a link to the tools in the account menu', async () => {
    await menu(true)
    expect(await screen.findByRole('link', { name: 'Operator tools' })).toHaveAttribute('href', '/admin')
  })

  it('shows nobody else any sign that the tools exist', async () => {
    await menu(false)
    await screen.findByText('Signed in as')
    expect(screen.queryByRole('link', { name: 'Operator tools' })).not.toBeInTheDocument()
  })
})

describe('the account list', () => {
  it('lists accounts with status, plan and where it came from, last sign-in and counters', async () => {
    await open()
    const region = await listed()
    expect(await within(region).findByText('dana.k@example.test')).toBeInTheDocument()
    const rows = within(region).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(5)
    const dana = rows[0] as HTMLElement
    expect(dana).toHaveTextContent('dana.k@example.test')
    expect(dana).toHaveTextContent('Active')
    expect(dana).toHaveTextContent('Pro (override)')
    expect(dana).toHaveTextContent('Oct 1, 2026')
    expect(dana).toHaveTextContent('1,204')
    expect(rows[1]).toHaveTextContent('Suspended')
    expect(rows[2]).toHaveTextContent('Pending deletion')
    expect(rows[2]).toHaveTextContent('—') // never signed in
    expect(rows[3]).toHaveTextContent('Pro (plan code)')
    expect(within(region).getByText('5 accounts')).toBeInTheDocument()
  })

  it('asks for the first page of 25, newest first as the server gives it', async () => {
    const { calls } = await open()
    await listed()
    await screen.findByText('dana.k@example.test')
    expect(requests(calls, '/api/admin/v1/users')[0]?.query).toEqual({ limit: '25', offset: '0' })
  })

  it('says so when nothing matches, and when the list cannot be loaded', async () => {
    await open({ users: [] })
    expect(await within(await listed()).findByText('No accounts match.')).toBeInTheDocument()
  })

  it('shows the server’s own sentence when the list is refused', async () => {
    await open({ listFails: { status: 403, detail: 'Confirm it is you with two-step verification first.' } })
    expect(await within(await listed()).findByRole('alert')).toHaveTextContent(
      'Confirm it is you with two-step verification first.',
    )
  })

  it('searches after a pause in typing, with the text trimmed, and starts again from page 1', async () => {
    const { calls, user: u } = await open()
    await listed()
    await u.type(screen.getByLabelText('Search accounts by email or id'), '  dana ')
    await waitFor(() => expect(requests(calls, '/api/admin/v1/users').at(-1)?.query['q']).toBe('dana'), {
      timeout: 3000,
    })
    await waitFor(() =>
      expect(within(screen.getByRole('region', { name: 'Accounts' })).getAllByRole('row')).toHaveLength(2),
    )
    expect(screen.getByText('1 account')).toBeInTheDocument()
  })

  it('searches at once on Enter', async () => {
    const { calls, user: u } = await open()
    await listed()
    await u.type(screen.getByLabelText('Search accounts by email or id'), '  leo {Enter}')
    await waitFor(() => expect(requests(calls, '/api/admin/v1/users').at(-1)?.query['q']).toBe('leo'))
  })

  it('does not search for every keystroke', async () => {
    const { calls, user: u } = await open()
    await listed()
    await screen.findByText('dana.k@example.test')
    const before = requests(calls, '/api/admin/v1/users').length
    await u.type(screen.getByLabelText('Search accounts by email or id'), 'abcdef')
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(requests(calls, '/api/admin/v1/users')).toHaveLength(before)
  })

  it('narrows to one status', async () => {
    const { calls, user: u } = await open()
    await listed()
    await u.selectOptions(screen.getByLabelText('Filter by status'), 'suspended')
    await waitFor(() => expect(requests(calls, '/api/admin/v1/users').at(-1)?.query['status']).toBe('suspended'))
    expect(await screen.findByText('1 account')).toBeInTheDocument()
  })

  it('keeps the search, the filter, the page and the open account in the address, so a view can be shared', async () => {
    const { router, user: u } = await open({}, { q: 'dana', status: 'active', user: DANA.id })
    expect(await screen.findByText('dana.k@example.test', { selector: 'h2' })).toBeInTheDocument()
    expect(router.state.location.search).toEqual({ q: 'dana', status: 'active', user: DANA.id })
    expect(screen.getByLabelText('Search accounts by email or id')).toHaveValue('dana')
    expect(screen.getByLabelText('Filter by status')).toHaveValue('active')
    await u.selectOptions(screen.getByLabelText('Filter by status'), '')
    await waitFor(() => expect(router.state.location.search).toEqual({ q: 'dana', user: DANA.id }))
  })

  it('ignores a malformed address instead of trusting it', async () => {
    const { calls } = await open({}, { status: 'deleted', page: 'x', user: 'not-an-id', q: 'x'.repeat(300) })
    await listed()
    expect(requests(calls, '/api/admin/v1/users')[0]?.query).toEqual({ q: 'x'.repeat(100), limit: '25', offset: '0' })
    expect(requests(calls, '/api/admin/v1/users/not-an-id')).toEqual([])
  })

  it('pages through more than 25 accounts, and says where it is', async () => {
    const many = Array.from({ length: 60 }, (_, i) =>
      user(
        `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`,
        `person${String(i).padStart(2, '0')}@example.test`,
      ),
    )
    const { calls, user: u } = await open({ users: many })
    const region = await listed()
    expect(await within(region).findByText('1–25 of 60')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Previous' })).toBeDisabled()
    await u.click(within(region).getByRole('button', { name: 'Next' }))
    expect(await within(region).findByText('26–50 of 60')).toBeInTheDocument()
    expect(requests(calls, '/api/admin/v1/users').at(-1)?.query).toMatchObject({ offset: '25' })
    await u.click(within(region).getByRole('button', { name: 'Next' }))
    expect(await within(region).findByText('51–60 of 60')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Next' })).toBeDisabled()
    await u.click(within(region).getByRole('button', { name: 'Previous' }))
    expect(await within(region).findByText('26–50 of 60')).toBeInTheDocument()
  })

  it('goes back to the first page when the search or the filter changes', async () => {
    const many = Array.from({ length: 60 }, (_, i) =>
      user(
        `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`,
        `person${String(i).padStart(2, '0')}@example.test`,
      ),
    )
    const { calls, router, user: u } = await open({ users: many })
    const region = await listed()
    await u.click(await within(region).findByRole('button', { name: 'Next' }))
    await within(region).findByText('26–50 of 60')
    expect(router.state.location.search).toEqual({ page: 2 })
    await u.selectOptions(screen.getByLabelText('Filter by status'), 'active')
    await within(region).findByText('1–25 of 60')
    expect(requests(calls, '/api/admin/v1/users').at(-1)?.query).toMatchObject({ status: 'active', offset: '0' })
    await u.click(within(region).getByRole('button', { name: 'Next' }))
    await within(region).findByText('26–50 of 60')
    await u.type(screen.getByLabelText('Search accounts by email or id'), 'person{Enter}')
    await within(region).findByText('1–25 of 60')
    expect(requests(calls, '/api/admin/v1/users').at(-1)?.query).toMatchObject({ q: 'person', offset: '0' })
  })

  it('asks again whether the second factor is still fresh, so one that has run out is noticed', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    try {
      const { calls } = await open()
      await listed()
      const sessions = () =>
        vi
          .mocked(globalThis.fetch)
          .mock.calls.filter(([r]) => new URL((r as Request).url).pathname === '/api/v1/session').length
      const before = sessions()
      await vi.advanceTimersByTimeAsync(61_000)
      expect(sessions()).toBeGreaterThan(before)
      expect(calls.length).toBeGreaterThan(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows no paging for a short list', async () => {
    await open()
    await screen.findByText('dana.k@example.test')
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
  })
})

describe('one account', () => {
  const dana = detail(DANA, {
    active_sessions: 2,
    overrides: [
      {
        id: 2,
        plan: 'pro',
        expires_at: '2026-12-31T23:59:59Z',
        reason: 'Beta tester, import bug goodwill',
        created_by: 'admin:x',
        created_at: '2026-09-22T10:02:00Z',
        revoked_at: null,
        revoked_by: null,
        revoke_reason: null,
      },
      {
        id: 1,
        plan: 'free',
        expires_at: null,
        reason: 'chargeback',
        created_by: 'admin:x',
        created_at: '2026-08-20T10:00:00Z',
        revoked_at: '2026-09-01T10:00:00Z',
        revoked_by: 'admin:x',
        revoke_reason: 'paid in full',
      },
    ],
    redemptions: [
      { code_label: 'Beta testers', plan: 'pro', starts_at: '2026-08-14T09:00:00Z', ends_at: '2026-11-12T09:00:00Z' },
    ],
    events: [
      { id: 9, at: '2026-10-02T08:14:00Z', actor: 'admin', action: 'admin_viewed_user', by: SELF, meta: {} },
      {
        id: 8,
        at: '2026-09-22T10:02:00Z',
        actor: 'admin',
        action: 'admin_plan_override_set',
        by: SELF,
        meta: { reason: 'press' },
      },
      { id: 7, at: '2026-10-01T19:40:00Z', actor: 'user', action: 'login', by: null, meta: {} },
      { id: 6, at: '2026-09-01T00:00:00Z', actor: 'system', action: 'something_new', by: null, meta: {} },
    ],
  })

  it('says to choose one before one is chosen, and asks the server for no detail', async () => {
    const { calls } = await open()
    await listed()
    expect(await panel()).toHaveTextContent('Choose an account to see its plan, counters and history.')
    expect(calls.filter((c) => /users\/[0-9a-f-]{36}/.test(c.path))).toEqual([])
  })

  it('opens an account from the list and asks for it once', async () => {
    const { calls, router, user: u } = await open({ details: { [DANA.id]: dana } })
    await listed()
    await u.click(await screen.findByRole('button', { name: 'dana.k@example.test' }))
    const account = await within(await panel()).findByRole('region', { name: 'Account' })
    expect(within(account).getByRole('heading', { name: 'dana.k@example.test' })).toBeInTheDocument()
    expect(account).toHaveTextContent('Joined Aug 14, 2026')
    expect(account).toHaveTextContent('2 active sessions')
    expect(account).toHaveTextContent('Pro')
    expect(account).toHaveTextContent('from an operator override')
    expect(account).toHaveTextContent('Dec 31, 2026')
    expect(account).toHaveTextContent('38 MB')
    expect(router.state.location.search).toEqual({ user: DANA.id })
    expect(requests(calls, `/api/admin/v1/users/${DANA.id}`)).toHaveLength(1) // every look is logged, so one look is one call
    await u.click(screen.getByRole('button', { name: 'dana.k@example.test' }))
    expect(within(await panel()).getByText(/Choose an account/)).toBeInTheDocument() // a second click puts it away
  })

  it('never says anything a person wrote, only counters and facts, and says that opening it is logged', async () => {
    await open({ details: { [DANA.id]: dana } }, { user: DANA.id })
    const account = await within(await panel()).findByRole('region', { name: 'Account' })
    expect(account).toHaveTextContent('Counters only: trades, notes and screenshots are never shown here.')
    expect(account).toHaveTextContent('Opening this page is recorded in the audit log.')
  })

  it('shows the override history, the plan codes used, and recent events in words', async () => {
    await open({ details: { [DANA.id]: dana } }, { user: DANA.id })
    const history = await within(await panel()).findByRole('region', { name: 'Override history' })
    expect(history).toHaveTextContent('Pro until Dec 31, 2026')
    expect(history).toHaveTextContent('in force')
    expect(history).toHaveTextContent('Beta tester, import bug goodwill')
    expect(history).toHaveTextContent('Free')
    expect(history).toHaveTextContent('revoked Sep 1, 2026')
    expect(history).toHaveTextContent('Revoked: paid in full')
    expect(history).toHaveTextContent('Beta testers')
    expect(history).toHaveTextContent('plan code')
    const events = within(await panel()).getByRole('region', { name: 'Recent events' })
    expect(
      within(events)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual([
      'An operator looked at this account · by an operatorOct 2, 2026 · 08:14 UTC',
      'Plan override set · by an operator · pressSep 22, 2026 · 10:02 UTC',
      'Signed inOct 1, 2026 · 19:40 UTC',
      'something newSep 1, 2026 · 00:00 UTC', // a word the page does not know is shown as written
    ])
  })

  it('says none yet for an account with no history', async () => {
    await open({}, { user: LEO.id })
    expect(await within(await panel()).findByRole('region', { name: 'Override history' })).toHaveTextContent(
      'None yet.',
    )
    expect(within(await panel()).getByRole('region', { name: 'Recent events' })).toHaveTextContent('Nothing yet.')
  })

  it('says when the person never subscribed', async () => {
    await open({}, { user: LEO.id })
    expect(await within(await panel()).findByRole('region', { name: 'Subscription' })).toHaveTextContent(
      'Never subscribed.',
    )
  })

  it('shows the subscription as the provider last told us, and its latest events without what they carried', async () => {
    const subscribed = detail(DANA, {
      subscription: {
        provider: 'paddle',
        provider_subscription_id: 'sub_01abc',
        provider_customer_id: 'ctm_01xyz',
        status: 'active',
        current_period_end: '2026-11-14T09:00:00Z',
        cancel_at_period_end: true,
        cancel_requested_by: 'deletion',
        past_due_since: null,
        ended_at: null,
      },
      billing_events: [
        { type: 'subscription.updated', outcome: 'applied', received_at: '2026-10-15T08:00:00Z' },
        { type: 'transaction.completed', outcome: 'duplicate', received_at: '2026-10-14T08:00:00Z' },
      ],
    })
    await open({ details: { [DANA.id]: subscribed } }, { user: DANA.id })
    const card = await within(await panel()).findByRole('region', { name: 'Subscription' })
    expect(card).toHaveTextContent('Paying · cancelled by the account deletion, ends Nov 14, 2026')
    expect(card).toHaveTextContent('paddle: sub_01abc · customer ctm_01xyz')
    expect(
      within(within(card).getByRole('list', { name: 'Billing events' }))
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual([
      'subscription.updated · appliedOct 15, 2026 · 08:00 UTC',
      'transaction.completed · duplicateOct 14, 2026 · 08:00 UTC',
    ])
  })

  it('says since when a payment has been failing, and when a subscription ended', async () => {
    const sub = {
      provider: 'paddle',
      provider_subscription_id: 'sub_1',
      provider_customer_id: null,
      current_period_end: '2026-10-14T09:00:00Z',
      cancel_at_period_end: false,
      cancel_requested_by: null,
      ended_at: null,
    } as const
    await open(
      {
        details: {
          [DANA.id]: detail(DANA, {
            subscription: { ...sub, status: 'past_due', past_due_since: '2026-10-14T09:05:00Z' },
          }),
        },
      },
      { user: DANA.id },
    )
    const failing = await within(await panel()).findByRole('region', { name: 'Subscription' })
    expect(failing).toHaveTextContent('Payment failed · renews Oct 14, 2026')
    expect(failing).toHaveTextContent('Payment failing since Oct 14, 2026')
    expect(failing).toHaveTextContent('paddle: sub_1')
    expect(failing).not.toHaveTextContent('customer')
  })

  it('says why when the account cannot be shown', async () => {
    await open({ detailFails: { status: 404, detail: 'There is no such account.' } }, { user: DANA.id })
    expect(await within(await panel()).findByRole('alert')).toHaveTextContent('There is no such account.')
  })

  it('shows only the changes that make sense for each status', async () => {
    const names = async (id: string) => {
      const view = await open({}, { user: id })
      const account = await within(await panel()).findByRole('region', { name: 'Account' })
      const found = within(account)
        .getAllByRole('button')
        .map((b) => b.textContent)
      view.unmount()
      return found
    }
    expect(await names(DANA.id)).toEqual(['Suspend', 'End sessions', 'Schedule deletion…'])
    expect(await names(LEO.id)).toEqual(['Restore', 'End sessions'])
    expect(await names(SAM.id)).toEqual(['Cancel the deletion'])
  })

  it('shows when a pending deletion happens', async () => {
    await open({}, { user: SAM.id })
    expect(await within(await panel()).findByRole('region', { name: 'Account' })).toHaveTextContent(
      'Deleted onOct 11, 2026',
    )
  })

  it('offers no change for another operator, and says why', async () => {
    await open({}, { user: OPERATOR.id })
    const account = await within(await panel()).findByRole('region', { name: 'Account' })
    expect(within(account).getByText('Operator')).toBeInTheDocument() // the badge
    expect(account).toHaveTextContent('Operators are changed with the command line, not here.')
    expect(within(account).queryByRole('button')).not.toBeInTheDocument()
    expect(within(await panel()).queryByRole('region', { name: 'Plan override' })).not.toBeInTheDocument()
  })

  it('offers no change for one’s own account either', async () => {
    await open({ users: [...ALL, ME] }, { user: SELF })
    const account = await within(await panel()).findByRole('region', { name: 'Account' })
    expect(account).toHaveTextContent('This is your own account. You cannot change it here.')
    expect(within(account).queryByRole('button')).not.toBeInTheDocument()
    expect(within(await panel()).queryByRole('region', { name: 'Plan override' })).not.toBeInTheDocument()
  })
})

describe('changing an account', () => {
  const ask = async (id: string, button: string) => {
    const view = await open({}, { user: id })
    const account = await within(await panel()).findByRole('region', { name: 'Account' })
    await view.user.click(within(account).getByRole('button', { name: button }))
    return { ...view, dialog: await screen.findByRole('dialog') }
  }

  it('asks why, says what will happen, and keeps the button off until there is a reason', async () => {
    const { dialog, user: u } = await ask(DANA.id, 'Suspend')
    expect(dialog).toHaveAccessibleName('Suspend this account?')
    expect(dialog).toHaveTextContent('dana.k@example.test')
    expect(dialog).toHaveTextContent('signed out everywhere now')
    const button = within(dialog).getByRole('button', { name: 'Suspend' })
    expect(button).toBeDisabled()
    await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), '   ')
    expect(button).toBeDisabled()
    await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), 'abuse of the import')
    expect(button).toBeEnabled()
  })

  it('sends the change with the trimmed reason and the CSRF token, then closes and reads the list again', async () => {
    const { calls, dialog, user: u } = await ask(DANA.id, 'Suspend')
    const before = requests(calls, '/api/admin/v1/users').length
    await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), '  abuse of the import  ')
    await u.click(within(dialog).getByRole('button', { name: 'Suspend' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(changes(calls)).toEqual([
      {
        method: 'POST',
        path: `/api/admin/v1/users/${DANA.id}/suspend`,
        query: {},
        body: { reason: 'abuse of the import' },
        csrf: 'tok',
      },
    ])
    await waitFor(() => expect(requests(calls, '/api/admin/v1/users').length).toBeGreaterThan(before))
  })

  it.each([
    [LEO.id, 'Restore', 'unsuspend'],
    [DANA.id, 'End sessions', 'revoke-sessions'],
    [DANA.id, 'Schedule deletion…', 'schedule-deletion'],
    [SAM.id, 'Cancel the deletion', 'cancel-deletion'],
  ])('%s: "%s" sends %s', async (id, button, path) => {
    const { calls, dialog, user: u } = await ask(id, button)
    await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), 'because')
    const buttons = within(dialog).getAllByRole('button')
    await u.click(buttons[buttons.length - 1] as HTMLElement)
    await waitFor(() => expect(changes(calls)).toHaveLength(1))
    expect(changes(calls)[0]).toMatchObject({
      method: 'POST',
      path: `/api/admin/v1/users/${id}/${path}`,
      body: { reason: 'because' },
    })
  })

  it('shows the server’s sentence when the change is refused, stays open, and can be tried again', async () => {
    const view = await open(
      { changeFails: { status: 409, detail: 'That account is not active, so it cannot be suspended.' } },
      { user: DANA.id },
    )
    const account = await within(await panel()).findByRole('region', { name: 'Account' })
    await view.user.click(within(account).getByRole('button', { name: 'Suspend' }))
    const dialog = await screen.findByRole('dialog')
    await view.user.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), 'x')
    await view.user.click(within(dialog).getByRole('button', { name: 'Suspend' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'That account is not active, so it cannot be suspended.',
    )
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Suspend' })).toBeEnabled()
  })

  it('can be cancelled without sending anything', async () => {
    const { calls, dialog, user: u } = await ask(DANA.id, 'Suspend')
    await u.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await u.keyboard('{Escape}')
    expect(changes(calls)).toEqual([])
  })

  it('cannot be closed or sent twice while the request is out', async () => {
    stubAdmin({ users: ALL })
    const normal = globalThis.fetch
    let answer: (response: Response) => void = () => undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((request: Request) =>
        request.method === 'POST' ? new Promise<Response>((resolve) => (answer = resolve)) : normal(request),
      ),
    )
    vi.spyOn(go, 'to').mockImplementation(() => undefined)
    openApp('/admin', { user: DANA.id })
    const u = userEvent.setup()
    const account = await within(await panel()).findByRole('region', { name: 'Account' })
    await u.click(within(account).getByRole('button', { name: 'Suspend' }))
    const dialog = await screen.findByRole('dialog')
    await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), 'x')
    await u.click(within(dialog).getByRole('button', { name: 'Suspend' }))
    expect(await within(dialog).findByRole('button', { name: 'Working…' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await u.keyboard('{Escape}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.submit(within(dialog).getByLabelText('Why? (kept in the audit log)'))
    answer(new Response(JSON.stringify(DANA), { status: 200 }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const posts = vi.mocked(globalThis.fetch).mock.calls.filter(([r]) => (r as Request).method === 'POST')
    expect(posts).toHaveLength(1)
  })
})

describe('the plan override', () => {
  const open_ = async (id = DANA.id, options: AdminStubOptions = {}) => {
    const view = await open(options, { user: id })
    const card = await within(await panel()).findByRole('region', { name: 'Plan override' })
    return { ...view, card }
  }

  it('says there is none, and keeps Save off until there is a reason', async () => {
    const { card, user: u } = await open_(LEO.id)
    expect(card).toHaveTextContent('No override.')
    expect(within(card).queryByRole('button', { name: 'Revoke override' })).not.toBeInTheDocument()
    const save = within(card).getByRole('button', { name: 'Save override' })
    expect(save).toBeDisabled()
    await u.type(within(card).getByLabelText('Reason (required)'), '  ')
    expect(save).toBeDisabled()
    await u.type(within(card).getByLabelText('Reason (required)'), 'press')
    expect(save).toBeEnabled()
  })

  it('sends the plan, the end as the last moment of that day in UTC, and the reason, then empties the reason', async () => {
    const { calls, card, user: u } = await open_(LEO.id)
    await u.selectOptions(within(card).getByLabelText('Plan'), 'free')
    fireEvent.change(within(card).getByLabelText('Ends (optional)'), { target: { value: '2026-12-31' } })
    await u.type(within(card).getByLabelText('Reason (required)'), '  refunded  ')
    await u.click(within(card).getByRole('button', { name: 'Save override' }))
    await waitFor(() => expect(changes(calls)).toHaveLength(1))
    expect(changes(calls)[0]).toEqual({
      method: 'PUT',
      path: `/api/admin/v1/users/${LEO.id}/plan-override`,
      query: {},
      body: { plan: 'free', expires_at: '2026-12-31T23:59:59Z', reason: 'refunded' },
      csrf: 'tok',
    })
    await waitFor(() => expect(within(card).getByLabelText('Reason (required)')).toHaveValue(''))
  })

  it('sends no end when none is given, and defaults to Pro', async () => {
    const { calls, card, user: u } = await open_(LEO.id)
    expect(within(card).getByLabelText('Plan')).toHaveValue('pro')
    await u.type(within(card).getByLabelText('Reason (required)'), 'press')
    await u.click(within(card).getByRole('button', { name: 'Save override' }))
    await waitFor(() => expect(changes(calls)).toHaveLength(1))
    expect(changes(calls)[0]?.body).toEqual({ plan: 'pro', expires_at: null, reason: 'press' })
  })

  it('shows the override in force, and revokes it with a reason', async () => {
    const withOverride = detail(DANA, {
      overrides: [
        {
          id: 2,
          plan: 'pro',
          expires_at: '2026-12-31T23:59:59Z',
          reason: 'Beta tester',
          created_by: 'admin:x',
          created_at: '2026-09-22T10:02:00Z',
          revoked_at: null,
          revoked_by: null,
          revoke_reason: null,
        },
      ],
    })
    const { calls, card, user: u } = await open_(DANA.id, { details: { [DANA.id]: withOverride } })
    expect(card).toHaveTextContent('In force: pro until Dec 31, 2026')
    const revoke = within(card).getByRole('button', { name: 'Revoke override' })
    expect(revoke).toBeDisabled()
    await u.type(within(card).getByLabelText('Reason (required)'), 'trial over')
    await u.click(revoke)
    await waitFor(() => expect(changes(calls)).toHaveLength(1))
    expect(changes(calls)[0]).toMatchObject({
      method: 'POST',
      path: `/api/admin/v1/users/${DANA.id}/plan-override/revoke`,
      body: { reason: 'trial over' },
    })
  })

  it('shows the server’s sentence when it is refused', async () => {
    const { card, user: u } = await open_(LEO.id, {
      changeFails: { status: 422, detail: 'An override has to end in the future (or not end at all).' },
    })
    await u.type(within(card).getByLabelText('Reason (required)'), 'x')
    await u.click(within(card).getByRole('button', { name: 'Save override' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('An override has to end in the future')
    expect(within(card).getByLabelText('Reason (required)')).toHaveValue('x') // kept, so it can be tried again
  })

  it('says it wins over everything and is never shown to the person', async () => {
    const { card } = await open_(LEO.id)
    expect(card).toHaveTextContent('An override wins over a plan code and a subscription')
    expect(card).toHaveTextContent('never shown to the person')
  })
})
