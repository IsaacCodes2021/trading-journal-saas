import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { go } from '../../lib/auth'
import { openApp } from '../../test-utils'
import { changes, event, grant, invite, stubAdmin, type AdminStubOptions, type Call } from './admin-stub'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function open(options: AdminStubOptions = {}, search: Record<string, string | number> = {}) {
  const calls = stubAdmin(options)
  vi.spyOn(go, 'to').mockImplementation(() => undefined)
  const app = openApp('/admin/codes', search)
  return { ...app, calls, user: userEvent.setup() }
}

const requests = (calls: Call[], path: string) => calls.filter((c) => c.path === path)
const listing = () => screen.findByRole('region', { name: /^(Invites|Plan codes)$/ })
const audit = () => screen.findByRole('region', { name: 'Audit log' })

const INVITES = [
  invite(3, 'Sam, futures group', { email: 'sam@example.test', used: 1, max_uses: 1, status: 'used_up' }),
  invite(2, 'Testers', {
    grant_code_id: 5,
    grant_label: 'Beta testers',
    used: 4,
    max_uses: 25,
    expires_at: '2026-12-31T00:00:00Z',
  }),
  invite(1, 'Old', { status: 'revoked', revoked_at: '2026-09-30T00:00:00Z' }),
]
const GRANTS = [
  grant(5, 'Beta testers', { used: 4, max_redemptions: 25, expires_at: '2026-12-31T00:00:00Z' }),
  grant(4, 'Leaked', { status: 'revoked', revoked_at: '2026-09-30T00:00:00Z' }),
]
const EVENTS = [
  event(30, 'admin_suspended', {
    actor: 'admin',
    actor_email: 'op@example.test',
    user_email: 'leo.w@example.test',
    user_id: 'u1',
    meta: { reason: 'abuse of the import' },
  }),
  event(20, 'admin_invite_created', {
    actor: 'admin',
    actor_email: 'op@example.test',
    meta: { invite: 3, label: 'Sam', max_uses: 1, days: null, for_one_address: true },
  }),
  event(10, 'login', { user_email: 'dana.k@example.test', user_id: 'u2' }),
]

describe('the overview', () => {
  it('shows the numbers an operator looks at first', async () => {
    await open()
    const tiles = await screen.findByRole('region', { name: 'Overview' })
    expect(tiles).toHaveTextContent('Accounts64')
    expect(tiles).toHaveTextContent('Signups, last 7 days18')
    expect(tiles).toHaveTextContent('Signed in, last 7 days41')
    expect(tiles).toHaveTextContent('57 Free · 7 Pro')
    expect(tiles).toHaveTextContent('2 · 2')
    expect(tiles).toHaveTextContent('3 · 1')
  })

  it('says so when the numbers cannot be loaded, and the rest of the page still works', async () => {
    await open({
      invites: INVITES,
      codesFail: { status: 500, detail: 'The server had a problem. Try again in a moment.' },
    })
    const alerts = await screen.findAllByRole('alert')
    expect(alerts[0]).toHaveTextContent('The server had a problem.')
    expect(screen.getByRole('tablist', { name: 'Codes' })).toBeInTheDocument()
  })
})

describe('the way in', () => {
  it('has Codes & audit in the operator navigation, and Users is not marked while on it', async () => {
    await open()
    await listing()
    const nav = screen.getByRole('navigation', { name: 'Operator' })
    expect(within(nav).getByRole('link', { name: 'Codes & audit' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Users' })).not.toHaveAttribute('aria-current')
  })

  it('is behind the same gate as the rest of the operator tools', async () => {
    const { calls } = await open({ mfaFresh: false })
    expect(await screen.findByRole('region', { name: 'Confirm it is you' })).toBeInTheDocument()
    expect(calls).toEqual([])
  })
})

describe('invites', () => {
  it('lists each with who it is for, the plan it carries, how many used it, when it ends, and where it stands', async () => {
    await open({ invites: INVITES, grantCodes: GRANTS })
    const region = await listing()
    const rows = (await within(region).findAllByRole('row')).slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Sam, futures group')
    expect(rows[0]).toHaveTextContent('sam@example.test')
    expect(rows[0]).toHaveTextContent('1 / 1')
    expect(rows[0]).toHaveTextContent('Used up')
    expect(rows[1]).toHaveTextContent('Beta testers')
    expect(rows[1]).toHaveTextContent('4 / 25')
    expect(rows[1]).toHaveTextContent('Dec 31, 2026')
    expect(rows[1]).toHaveTextContent('Works')
    expect(rows[2]).toHaveTextContent('Revoked')
  })

  it('offers Revoke on every invite that is not already revoked', async () => {
    await open({ invites: INVITES })
    const region = await listing()
    await within(region).findByText('Testers')
    expect(within(region).getAllByRole('button', { name: /^Revoke the invite/ })).toHaveLength(2)
    expect(within(region).queryByRole('button', { name: 'Revoke the invite Old' })).not.toBeInTheDocument()
  })

  it('says there are none yet', async () => {
    await open()
    expect(await within(await listing()).findByText('No invites yet.')).toBeInTheDocument()
  })

  it('pages through more than 25', async () => {
    const many = Array.from({ length: 60 }, (_, i) => invite(i + 1, `invite ${i + 1}`))
    const { calls, router, user: u } = await open({ invites: many })
    const region = await listing()
    expect(await within(region).findByText('1–25 of 60')).toBeInTheDocument()
    await u.click(within(region).getByRole('button', { name: 'Next' }))
    expect(await within(region).findByText('26–50 of 60')).toBeInTheDocument()
    expect(requests(calls, '/api/admin/v1/invites').at(-1)?.query).toMatchObject({ offset: '25', limit: '25' })
    expect(router.state.location.search).toMatchObject({ page: 2 })
    expect(within(region).getByRole('button', { name: 'Previous' })).toBeEnabled()
  })

  describe('revoking one', () => {
    const ask = async (options: AdminStubOptions = {}) => {
      const view = await open({ invites: INVITES, ...options })
      const region = await listing()
      await view.user.click(await within(region).findByRole('button', { name: 'Revoke the invite Testers' }))
      return { ...view, dialog: await screen.findByRole('dialog') }
    }

    it('asks why, says what it does, and keeps the button off until there is a reason', async () => {
      const { dialog, user: u } = await ask()
      expect(dialog).toHaveAccessibleName('Revoke this invite?')
      expect(dialog).toHaveTextContent('Testers')
      expect(dialog).toHaveTextContent('People who already used it keep')
      const button = within(dialog).getByRole('button', { name: 'Revoke invite' })
      expect(button).toBeDisabled()
      await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), '   ')
      expect(button).toBeDisabled()
      await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), 'sent to the wrong person')
      expect(button).toBeEnabled()
    })

    it('sends it with the trimmed reason and the CSRF token, closes, and reads the list again', async () => {
      const { calls, dialog, user: u } = await ask()
      const before = requests(calls, '/api/admin/v1/invites').length
      await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), '  wrong person ')
      await u.click(within(dialog).getByRole('button', { name: 'Revoke invite' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(changes(calls)).toEqual([
        {
          method: 'POST',
          path: '/api/admin/v1/invites/2/revoke',
          query: {},
          body: { reason: 'wrong person' },
          csrf: 'tok',
        },
      ])
      await waitFor(() => expect(requests(calls, '/api/admin/v1/invites').length).toBeGreaterThan(before))
    })

    it('shows the server’s sentence when it is refused and stays open', async () => {
      const { dialog, user: u } = await ask({ changeFails: { status: 409, detail: 'That invite is already revoked.' } })
      await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), 'x')
      await u.click(within(dialog).getByRole('button', { name: 'Revoke invite' }))
      expect(await within(dialog).findByRole('alert')).toHaveTextContent('That invite is already revoked.')
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    it('can be cancelled without sending anything', async () => {
      const { calls, dialog, user: u } = await ask()
      await u.click(within(dialog).getByRole('button', { name: 'Cancel' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(changes(calls)).toEqual([])
    })

    it('cannot be closed or sent twice while the request is out', async () => {
      stubAdmin({ invites: INVITES })
      const normal = globalThis.fetch
      let answer: (response: Response) => void = () => undefined
      vi.stubGlobal(
        'fetch',
        vi.fn((request: Request) =>
          request.method === 'POST' ? new Promise<Response>((resolve) => (answer = resolve)) : normal(request),
        ),
      )
      vi.spyOn(go, 'to').mockImplementation(() => undefined)
      openApp('/admin/codes')
      const u = userEvent.setup()
      await u.click(await within(await listing()).findByRole('button', { name: 'Revoke the invite Testers' }))
      const dialog = await screen.findByRole('dialog')
      const reason = within(dialog).getByLabelText('Why? (kept in the audit log)')
      await u.type(reason, 'x')
      await u.click(within(dialog).getByRole('button', { name: 'Revoke invite' }))
      expect(await within(dialog).findByRole('button', { name: 'Working…' })).toBeDisabled()
      expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled()
      await u.keyboard('{Escape}')
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      fireEvent.submit(reason)
      answer(new Response(JSON.stringify({ ...INVITES[1], status: 'revoked' }), { status: 200 }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      const posts = vi.mocked(globalThis.fetch).mock.calls.filter(([r]) => (r as Request).method === 'POST')
      expect(posts).toHaveLength(1)
    })
  })
})

describe('making an invite', () => {
  const form = async (options: AdminStubOptions = {}) => {
    const view = await open(options)
    const card = await screen.findByRole('region', { name: 'Make an invite' })
    return { ...view, card }
  }

  it('sends what was typed, the label and address trimmed, and shows the new code once', async () => {
    const { calls, card, user: u } = await form({ grantCodes: GRANTS })
    await u.type(within(card).getByLabelText('Label'), '  Sam, futures group ')
    await u.type(within(card).getByLabelText('Only this email address (optional)'), ' sam@example.test ')
    await u.clear(within(card).getByLabelText('How many may use it'))
    await u.type(within(card).getByLabelText('How many may use it'), '3')
    await u.type(within(card).getByLabelText('Works for (days, optional)'), '14')
    await u.click(within(card).getByRole('button', { name: 'Make invite' }))
    const banner = await screen.findByRole('region', { name: 'New code' })
    expect(within(banner).getByTestId('new-code')).toHaveTextContent('TJ-NEW1-CODE')
    expect(banner).toHaveTextContent('This is the only time the code is shown.')
    expect(changes(calls)).toEqual([
      {
        method: 'POST',
        path: '/api/admin/v1/invites',
        query: {},
        body: { label: 'Sam, futures group', email: 'sam@example.test', max_uses: 3, days: 14, grant_code_id: null },
        csrf: 'tok',
      },
    ])
  })

  it('defaults to one use, no end, no address and no plan, and empties the form after it worked', async () => {
    const { calls, card, user: u } = await form()
    expect(within(card).getByLabelText('How many may use it')).toHaveValue('1')
    await u.type(within(card).getByLabelText('Label'), 'Sam')
    await u.click(within(card).getByRole('button', { name: 'Make invite' }))
    await screen.findByRole('region', { name: 'New code' })
    expect(changes(calls)[0]?.body).toEqual({ label: 'Sam', email: null, max_uses: 1, days: null, grant_code_id: null })
    await waitFor(() => expect(within(card).getByLabelText('Label')).toHaveValue(''))
  })

  it('offers only the plan codes that still work, and sends the one chosen', async () => {
    const { calls, card, user: u } = await form({ grantCodes: GRANTS })
    const select = within(card).getByLabelText('Plan it carries (optional)')
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(2))
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['None', 'Beta testers (90 days of Pro)'])
    await u.selectOptions(select, '5')
    await u.click(within(card).getByRole('button', { name: 'Make invite' }))
    await screen.findByRole('region', { name: 'New code' })
    expect(changes(calls)[0]?.body).toMatchObject({ grant_code_id: 5 })
  })

  it.each([
    ['0', '', 'Uses must be a whole number from 1 to 1000.'],
    ['1001', '', 'Uses must be a whole number from 1 to 1000.'],
    ['two', '', 'Uses must be a whole number from 1 to 1000.'],
    ['', '', 'Uses must be a whole number from 1 to 1000.'],
    ['1', '0', 'Days must be a whole number from 1 to 365, or empty for no end.'],
    ['1', '366', 'Days must be a whole number from 1 to 365, or empty for no end.'],
    ['1', '1.5', 'Days must be a whole number from 1 to 365, or empty for no end.'],
  ])('refuses uses %j and days %j before sending anything', async (uses, days, message) => {
    const { calls, card, user: u } = await form()
    await u.clear(within(card).getByLabelText('How many may use it'))
    if (uses) await u.type(within(card).getByLabelText('How many may use it'), uses)
    if (days) await u.type(within(card).getByLabelText('Works for (days, optional)'), days)
    await u.click(within(card).getByRole('button', { name: 'Make invite' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent(message)
    expect(changes(calls)).toEqual([])
  })

  it('shows the server’s sentence when it is refused, and keeps what was typed', async () => {
    const { card, user: u } = await form({
      changeFails: { status: 422, detail: 'That plan code does not exist, has expired or has been revoked.' },
    })
    await u.type(within(card).getByLabelText('Label'), 'Sam')
    await u.click(within(card).getByRole('button', { name: 'Make invite' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('That plan code does not exist')
    expect(within(card).getByLabelText('Label')).toHaveValue('Sam')
    expect(screen.queryByRole('region', { name: 'New code' })).not.toBeInTheDocument()
  })

  it('shows the code until it is put away, and then it is gone', async () => {
    const { card, user: u } = await form()
    await u.click(within(card).getByRole('button', { name: 'Make invite' }))
    const banner = await screen.findByRole('region', { name: 'New code' })
    await u.click(within(banner).getByRole('button', { name: 'I have copied it' }))
    expect(screen.queryByRole('region', { name: 'New code' })).not.toBeInTheDocument()
    expect(screen.queryByText('TJ-NEW1-CODE')).not.toBeInTheDocument()
  })

  it('copies the code on request and says so, or says it could not', async () => {
    const { card, user: u } = await form()
    const write = vi.fn(async () => undefined) // after the setup, which puts a clipboard of its own in place
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true })
    await u.click(within(card).getByRole('button', { name: 'Make invite' }))
    const banner = await screen.findByRole('region', { name: 'New code' })
    await u.click(within(banner).getByRole('button', { name: 'Copy' }))
    expect(write).toHaveBeenCalledWith('TJ-NEW1-CODE')
    expect(await within(banner).findByRole('status')).toHaveTextContent('Copied.')
    write.mockRejectedValueOnce(new Error('blocked'))
    await u.click(within(banner).getByRole('button', { name: 'Copy' }))
    expect(await within(banner).findByRole('alert')).toHaveTextContent('Could not copy.')
  })

  it('does not send twice while it is out', async () => {
    stubAdmin()
    const normal = globalThis.fetch
    let answer: (response: Response) => void = () => undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((request: Request) =>
        request.method === 'POST' ? new Promise<Response>((resolve) => (answer = resolve)) : normal(request),
      ),
    )
    openApp('/admin/codes')
    const u = userEvent.setup()
    const card = await screen.findByRole('region', { name: 'Make an invite' })
    await u.click(within(card).getByRole('button', { name: 'Make invite' }))
    expect(await within(card).findByRole('button', { name: 'Making…' })).toBeDisabled()
    fireEvent.submit(within(card).getByLabelText('Label'))
    answer(new Response(JSON.stringify({ invite: invite(9, ''), code: 'TJ-NEW1-CODE' }), { status: 200 }))
    await screen.findByRole('region', { name: 'New code' })
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(([r]) => (r as Request).method === 'POST')).toHaveLength(1)
  })
})

describe('plan codes', () => {
  const tab = async (options: AdminStubOptions = {}) => {
    const view = await open({ grantCodes: GRANTS, ...options }, { tab: 'codes' })
    return { ...view, region: await screen.findByRole('region', { name: 'Plan codes' }) }
  }

  it('lists each with what it gives, how many people used it, when it ends and where it stands', async () => {
    const { region } = await tab()
    const rows = (await within(region).findAllByRole('row')).slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Beta testers')
    expect(rows[0]).toHaveTextContent('90 days of Pro')
    expect(rows[0]).toHaveTextContent('4 / 25')
    expect(rows[0]).toHaveTextContent('Dec 31, 2026')
    expect(rows[1]).toHaveTextContent('Revoked')
    expect(within(rows[1] as HTMLElement).queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows a count with no limit as just the number', async () => {
    const { region } = await tab({ grantCodes: [grant(1, 'Anyone', { used: 6 })] })
    const row = (await within(region).findAllByRole('row'))[1] as HTMLElement
    expect(row).toHaveTextContent(/Anyone90 days of Pro6—/)
  })

  it('is on its own tab, and the tab is in the address', async () => {
    const { router, user: u } = await open({ invites: INVITES, grantCodes: GRANTS })
    await listing()
    await u.click(screen.getByRole('tab', { name: 'Plan codes' }))
    await screen.findByRole('region', { name: 'Plan codes' })
    expect(router.state.location.search).toEqual({ tab: 'codes' })
    expect(screen.getByRole('tab', { name: 'Plan codes' })).toHaveAttribute('aria-selected', 'true')
    await u.click(screen.getByRole('tab', { name: 'Invites' }))
    await screen.findByRole('region', { name: 'Invites' })
    expect(router.state.location.search).toEqual({})
  })

  it('makes one, sends what was typed, and shows the code once', async () => {
    const { calls, user: u } = await tab()
    const card = screen.getByRole('region', { name: 'Make a plan code' })
    await u.type(within(card).getByLabelText('Label'), '  Beta testers ')
    await u.clear(within(card).getByLabelText('Days of Pro'))
    await u.type(within(card).getByLabelText('Days of Pro'), '60')
    await u.type(within(card).getByLabelText('How many people (optional)'), '25')
    await u.type(within(card).getByLabelText('The code ends after (days, optional)'), '30')
    await u.click(within(card).getByRole('button', { name: 'Make plan code' }))
    const banner = await screen.findByRole('region', { name: 'New code' })
    expect(within(banner).getByTestId('new-code')).toHaveTextContent('GR-NEW1-CODE')
    expect(changes(calls)).toEqual([
      {
        method: 'POST',
        path: '/api/admin/v1/grant-codes',
        query: {},
        body: { label: 'Beta testers', days: 60, max_redemptions: 25, expires_in_days: 30 },
        csrf: 'tok',
      },
    ])
  })

  it('defaults to 90 days, any number of people and no end', async () => {
    const { calls, user: u } = await tab()
    const card = screen.getByRole('region', { name: 'Make a plan code' })
    expect(within(card).getByLabelText('Days of Pro')).toHaveValue('90')
    await u.type(within(card).getByLabelText('Label'), 'x')
    await u.click(within(card).getByRole('button', { name: 'Make plan code' }))
    await screen.findByRole('region', { name: 'New code' })
    expect(changes(calls)[0]?.body).toEqual({ label: 'x', days: 90, max_redemptions: null, expires_in_days: null })
  })

  it.each([
    ['', '90', '', '', 'Give it a label, so you can tell it apart later.'],
    ['x', '0', '', '', 'Days of Pro must be a whole number from 1 to 3650.'],
    ['x', '3651', '', '', 'Days of Pro must be a whole number from 1 to 3650.'],
    ['x', '', '', '', 'Days of Pro must be a whole number from 1 to 3650.'],
    ['x', '90', '0', '', 'People must be a whole number of at least 1, or empty for any number.'],
    ['x', '90', 'many', '', 'People must be a whole number of at least 1, or empty for any number.'],
    ['x', '90', '1000001', '', 'People must be a whole number of at least 1, or empty for any number.'],
    ['x', '90', '', '0', 'The code can end after 1 to 3650 days, or never if empty.'],
    ['x', '90', '', '3651', 'The code can end after 1 to 3650 days, or never if empty.'],
  ])(
    'refuses label %j, days %j, people %j, ends %j before sending anything',
    async (label, days, people, ends, message) => {
      const { calls, user: u } = await tab()
      const card = screen.getByRole('region', { name: 'Make a plan code' })
      await u.clear(within(card).getByLabelText('Days of Pro'))
      if (label) await u.type(within(card).getByLabelText('Label'), label)
      if (days) await u.type(within(card).getByLabelText('Days of Pro'), days)
      if (people) await u.type(within(card).getByLabelText('How many people (optional)'), people)
      if (ends) await u.type(within(card).getByLabelText('The code ends after (days, optional)'), ends)
      await u.click(within(card).getByRole('button', { name: 'Make plan code' }))
      expect(await within(card).findByRole('alert')).toHaveTextContent(message)
      expect(changes(calls)).toEqual([])
    },
  )

  it('accepts the largest values it allows', async () => {
    const { calls, user: u } = await tab()
    const card = screen.getByRole('region', { name: 'Make a plan code' })
    await u.type(within(card).getByLabelText('Label'), 'Most')
    await u.clear(within(card).getByLabelText('Days of Pro'))
    await u.type(within(card).getByLabelText('Days of Pro'), '3650')
    await u.type(within(card).getByLabelText('How many people (optional)'), '1000000')
    await u.type(within(card).getByLabelText('The code ends after (days, optional)'), '3650')
    await u.click(within(card).getByRole('button', { name: 'Make plan code' }))
    await screen.findByRole('region', { name: 'New code' })
    expect(changes(calls)[0]?.body).toEqual({
      label: 'Most',
      days: 3650,
      max_redemptions: 1_000_000,
      expires_in_days: 3650,
    })
  })

  it('pages through more than 25, and the tab stays in the address', async () => {
    const many = Array.from({ length: 60 }, (_, i) => grant(i + 1, `code ${i + 1}`))
    const { calls, region, router, user: u } = await tab({ grantCodes: many })
    expect(await within(region).findByText('1–25 of 60')).toBeInTheDocument()
    await u.click(within(region).getByRole('button', { name: 'Next' }))
    expect(await within(region).findByText('26–50 of 60')).toBeInTheDocument()
    expect(requests(calls, '/api/admin/v1/grant-codes').at(-1)?.query).toMatchObject({ offset: '25', limit: '25' })
    expect(router.state.location.search).toEqual({ tab: 'codes', page: 2 })
  })

  it('says that a plan code unlocks nothing yet', async () => {
    await tab()
    expect(screen.getByRole('region', { name: 'Make a plan code' })).toHaveTextContent('unlocks nothing yet')
  })

  it('revokes one with a reason', async () => {
    const { calls, user: u, region } = await tab()
    await u.click(await within(region).findByRole('button', { name: 'Revoke the plan code Beta testers' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAccessibleName('Revoke this plan code?')
    await u.type(within(dialog).getByLabelText('Why? (kept in the audit log)'), 'leaked')
    await u.click(within(dialog).getByRole('button', { name: 'Revoke plan code' }))
    await waitFor(() => expect(changes(calls)).toHaveLength(1))
    expect(changes(calls)[0]).toMatchObject({
      method: 'POST',
      path: '/api/admin/v1/grant-codes/5/revoke',
      body: { reason: 'leaked' },
    })
  })
})

describe('the audit log', () => {
  it('shows each event with when, who, what, about whom and the detail, newest first', async () => {
    await open({ auditEvents: EVENTS })
    const region = await audit()
    const rows = (await within(region).findAllByRole('row')).slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Oct 2, 2026 · 08:14 UTC')
    expect(rows[0]).toHaveTextContent('op@example.test')
    expect(rows[0]).toHaveTextContent('Suspended')
    expect(rows[0]).toHaveTextContent('leo.w@example.test')
    expect(rows[0]).toHaveTextContent('abuse of the import')
    expect(rows[1]).toHaveTextContent('Invite made')
    expect(rows[1]).toHaveTextContent('invite 3 · label Sam · max uses 1 · for one address')
    expect(rows[2]).toHaveTextContent('The person')
    expect(rows[2]).toHaveTextContent('Signed in')
    expect(rows[2]).toHaveTextContent('dana.k@example.test')
  })

  it('asks for the last 7 days, 50 at a time, by default', async () => {
    const { calls } = await open({ auditEvents: EVENTS })
    await within(await audit()).findAllByRole('row')
    expect(requests(calls, '/api/admin/v1/audit')[0]?.query).toEqual({ days: '7', limit: '50' })
  })

  it('narrows by what happened, who did it and how far back, and starts over', async () => {
    const { calls, router, user: u } = await open({ auditEvents: EVENTS })
    const region = await audit()
    await u.selectOptions(within(region).getByLabelText('Filter by what happened'), 'admin_suspended')
    await waitFor(() =>
      expect(requests(calls, '/api/admin/v1/audit').at(-1)?.query).toMatchObject({ action: 'admin_suspended' }),
    )
    expect(await within(region).findAllByRole('row')).toHaveLength(2)
    await u.selectOptions(within(region).getByLabelText('Filter by who did it'), 'admin')
    await waitFor(() =>
      expect(requests(calls, '/api/admin/v1/audit').at(-1)?.query).toMatchObject({
        action: 'admin_suspended',
        actor: 'admin',
      }),
    )
    await u.selectOptions(within(region).getByLabelText('How far back'), '0')
    await waitFor(() => expect(requests(calls, '/api/admin/v1/audit').at(-1)?.query['days']).toBeUndefined())
    expect(router.state.location.search).toEqual({ action: 'admin_suspended', actor: 'admin', days: 0 })
    await u.selectOptions(within(region).getByLabelText('How far back'), '30')
    await waitFor(() => expect(requests(calls, '/api/admin/v1/audit').at(-1)?.query['days']).toBe('30'))
  })

  it('searches for a person after a pause, trimmed, and at once on Enter', async () => {
    const { calls, user: u } = await open({ auditEvents: EVENTS })
    const region = await audit()
    await u.type(within(region).getByLabelText('Search the log by email or id'), '  dana ')
    await waitFor(() => expect(requests(calls, '/api/admin/v1/audit').at(-1)?.query['q']).toBe('dana'), {
      timeout: 3000,
    })
    await waitFor(() => expect(within(region).getAllByRole('row')).toHaveLength(2))
    await u.clear(within(region).getByLabelText('Search the log by email or id'))
    await u.type(within(region).getByLabelText('Search the log by email or id'), 'leo{Enter}')
    await waitFor(() => expect(requests(calls, '/api/admin/v1/audit').at(-1)?.query['q']).toBe('leo'))
  })

  it('does not search for every keystroke', async () => {
    const { calls, user: u } = await open({ auditEvents: EVENTS })
    const region = await audit()
    await within(region).findAllByRole('row')
    const before = requests(calls, '/api/admin/v1/audit').length
    await u.type(within(region).getByLabelText('Search the log by email or id'), 'abcdef')
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(requests(calls, '/api/admin/v1/audit')).toHaveLength(before)
  })

  it('reads older events a page at a time, using where the last page said the next begins', async () => {
    const many = Array.from({ length: 120 }, (_, i) =>
      event(120 - i, 'login', { user_email: `p${120 - i}@example.test` }),
    )
    const { calls, user: u } = await open({ auditEvents: many })
    const region = await audit()
    await waitFor(() => expect(within(region).getAllByRole('row')).toHaveLength(51))
    await u.click(within(region).getByRole('button', { name: 'Older events' }))
    await waitFor(() => expect(within(region).getAllByRole('row')).toHaveLength(101))
    expect(requests(calls, '/api/admin/v1/audit').at(-1)?.query).toMatchObject({ before: '71' })
    await u.click(within(region).getByRole('button', { name: 'Older events' }))
    await waitFor(() => expect(within(region).getAllByRole('row')).toHaveLength(121))
    expect(within(region).queryByRole('button', { name: 'Older events' })).not.toBeInTheDocument() // the last page
    expect(within(region).getAllByRole('row')[1]).toHaveTextContent('p120@example.test') // still newest first
  })

  it('says nothing matches, and shows the server’s sentence when it cannot be read', async () => {
    const view = await open({ auditEvents: [] })
    expect(await within(await audit()).findByText('Nothing matches.')).toBeInTheDocument()
    view.unmount()
    await open({
      auditEvents: EVENTS,
      codesFail: { status: 403, detail: 'Confirm it is you with two-step verification first.' },
    })
    expect(await within(await audit()).findByRole('alert')).toHaveTextContent(
      'Confirm it is you with two-step verification first.',
    )
  })

  it('says "An account" for one that is gone, and a dash when the event is about nobody', async () => {
    await open({
      auditEvents: [
        event(3, 'login', { user_id: 'gone', user_email: null }),
        event(2, 'admin_invite_created', { actor: 'admin', actor_email: 'op@example.test' }),
      ],
    })
    const rows = (await within(await audit()).findAllByRole('row')).slice(1)
    expect(rows[0]).toHaveTextContent('An account')
    expect(rows[1]).not.toHaveTextContent('An account')
    expect(rows[1]).toHaveTextContent('—')
  })

  it('shows an unknown kind of event as written, and keeps a filter the page has no words for', async () => {
    await open(
      { auditEvents: [event(1, 'something_new', { actor: 'cli:ops' })] },
      { action: 'something_new', actor: 'cli:ops' },
    )
    const region = await audit()
    expect(await within(region).findByText('something new')).toBeInTheDocument()
    expect(within(region).getByLabelText('Filter by what happened')).toHaveValue('something_new')
    expect(within(region).getByLabelText('Filter by who did it')).toHaveValue('cli:ops')
  })

  it('takes its filters from the address, and ignores a malformed one', async () => {
    const { calls } = await open(
      { auditEvents: EVENTS },
      { action: "x'; drop", actor: '../x', days: 5, q: 'dana', page: 'x' },
    )
    await within(await audit()).findAllByRole('row')
    expect(requests(calls, '/api/admin/v1/audit')[0]?.query).toEqual({ q: 'dana', days: '7', limit: '50' })
  })
})
