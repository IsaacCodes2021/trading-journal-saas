import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openApp } from '../../test-utils'
import { changes, person, stubAdmin, type AdminStubOptions, type Call } from './admin-stub'

// Only the clock is faked, so "23 days left" does not depend on the day the tests run; timers and promises stay real.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-16T09:00:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const ALEX = person(1, 'Alex', { access_ends_at: '2026-11-08T09:00:00Z', joined_at: '2026-10-09T09:00:00Z' })
const PRIYA = person(2, 'Priya', {
  prop_firm: 'Apex',
  access_ends_at: '2026-11-07T09:00:00Z',
  joined_at: '2026-10-08T09:00:00Z',
})
const JORDAN = person(3, 'Jordan', {
  prop_firm: 'Lucid',
  state: 'waiting',
  code: 'unused',
  access_ends_at: null,
  joined_at: '2026-10-08T12:00:00Z',
})
const DANA = person(4, 'Dana', {
  prop_firm: '',
  state: 'waiting',
  code: 'expired',
  access_ends_at: null,
  code_email_sent_at: null,
  code_email_failures: 2,
  joined_at: '2026-09-20T12:00:00Z',
})
const SAM = person(5, 'Sam', {
  state: 'ended',
  access_ends_at: '2026-10-01T09:00:00Z',
  notice_sent_at: '2026-10-01T11:00:00Z',
  joined_at: '2026-09-01T09:00:00Z',
})
const CHRIS = person(6, 'Chris', {
  prop_firm: 'Apex',
  state: 'revoked',
  revoked_at: '2026-10-05T10:00:00Z',
  access_ends_at: '2026-10-20T09:00:00Z',
  notice_sent_at: '2026-10-05T10:00:01Z',
  joined_at: '2026-09-20T09:00:00Z',
})
const PEOPLE = [ALEX, PRIYA, JORDAN, DANA, SAM, CHRIS]

async function open(options: AdminStubOptions = {}, search: Record<string, string | number> = {}) {
  const calls = stubAdmin({ beta: PEOPLE, ...options })
  const app = openApp('/admin/beta', search)
  return { ...app, calls, user: userEvent.setup() }
}

const listed = () => screen.findByRole('region', { name: 'Beta testers' })
const panel = () => screen.findByRole('complementary', { name: 'Selected person' })
const requests = (calls: Call[]) => calls.filter((c) => c.path === '/api/admin/v1/beta')
const rowsOf = (region: HTMLElement) => within(region).getAllByRole('row').slice(1)

describe('the way in', () => {
  it('is in the operator navigation, between Users and Codes & audit, and marks itself as the current page', async () => {
    await open()
    await listed()
    const nav = screen.getByRole('navigation', { name: 'Operator' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual(['Users', 'Beta testers', 'Codes & audit', 'Back to the app'])
    expect(within(nav).getByRole('link', { name: 'Beta testers' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Users' })).not.toHaveAttribute('aria-current')
  })

  it('is behind the same gate as the rest of the operator tools', async () => {
    const { calls } = await open({ mfaFresh: false })
    expect(await screen.findByRole('region', { name: 'Confirm it is you' })).toBeInTheDocument()
    expect(calls).toEqual([])
  })

  it('shows nothing, and asks for nothing, to someone who is not an operator', async () => {
    const { calls } = await open({ operator: false })
    expect(await screen.findByText('This area is for operators.')).toBeInTheDocument()
    expect(calls).toEqual([])
  })

  it('says what is and is not shown', async () => {
    await open()
    expect(await screen.findByRole('note')).toHaveTextContent('Trades, notes and screenshots are never shown')
  })
})

describe('the list', () => {
  it('shows each person: name, address, firm, when they joined, and where their 30 days stand', async () => {
    await open()
    const region = await listed()
    await within(region).findByRole('table')
    const rows = rowsOf(region)
    expect(rows).toHaveLength(6)
    expect(rows[0]).toHaveTextContent('Alex')
    expect(rows[0]).toHaveTextContent('alex@example.test')
    expect(rows[0]).toHaveTextContent('Topstep')
    expect(rows[0]).toHaveTextContent('Oct 9, 2026')
    expect(rows[0]).toHaveTextContent('Active · 23 days left')
    expect(rows[1]).toHaveTextContent('Active · 22 days left')
    expect(rows[2]).toHaveTextContent('Waiting · code unused')
    expect(rows[3]).toHaveTextContent('Waiting · code expired')
    expect(rows[3]).toHaveTextContent('—') // no firm given
    expect(rows[4]).toHaveTextContent('Ended · Oct 1, 2026')
    expect(rows[5]).toHaveTextContent('Revoked · Oct 5, 2026')
  })

  it('counts one day left as "1 day", and a part of a day as a whole day', async () => {
    await open({ beta: [person(1, 'Soon', { access_ends_at: '2026-10-16T21:00:00Z' })] })
    expect(await screen.findByText('Active · 1 day left')).toBeInTheDocument()
  })

  it('counts each state on its filter, and all of them', async () => {
    await open()
    const filter = await screen.findByRole('group', { name: 'Filter by state' })
    await within(filter).findByRole('button', { name: /^All/ })
    expect(within(filter).getByRole('button', { name: 'All 6' })).toBeInTheDocument()
    expect(within(filter).getByRole('button', { name: 'Waiting 2' })).toBeInTheDocument()
    expect(within(filter).getByRole('button', { name: 'Active 2' })).toBeInTheDocument()
    expect(within(filter).getByRole('button', { name: 'Ended 1' })).toBeInTheDocument()
    expect(within(filter).getByRole('button', { name: 'Revoked 1' })).toBeInTheDocument()
  })

  it('narrows to one state, starts again from the first page, and keeps the state in the address', async () => {
    const { calls, router, user: u } = await open()
    const filter = await screen.findByRole('group', { name: 'Filter by state' })
    await u.click(await within(filter).findByRole('button', { name: 'Waiting 2' }))
    await waitFor(() => expect(requests(calls).at(-1)?.query).toMatchObject({ state: 'waiting', offset: '0' }))
    await waitFor(() => expect(rowsOf(screen.getByRole('region', { name: 'Beta testers' }))).toHaveLength(2))
    expect(within(filter).getByRole('button', { name: 'Waiting 2' })).toHaveAttribute('aria-pressed', 'true')
    expect(router.state.location.search).toEqual({ state: 'waiting' })
    await u.click(within(filter).getByRole('button', { name: 'All 6' }))
    await waitFor(() => expect(router.state.location.search).toEqual({}))
  })

  it('goes back to the first page when the state changes', async () => {
    const many = Array.from({ length: 60 }, (_, i) => person(i + 1, `Person${String(i).padStart(2, '0')}`))
    const { calls, router, user: u } = await open({ beta: many })
    const region = await listed()
    await u.click(await within(region).findByRole('button', { name: 'Next' }))
    await within(region).findByText('26–50 of 60')
    expect(router.state.location.search).toEqual({ page: 2 })
    await u.click(within(region).getByRole('button', { name: /^Active/ }))
    await within(region).findByText('1–25 of 60')
    expect(requests(calls).at(-1)?.query).toMatchObject({ state: 'active', offset: '0' })
    expect(router.state.location.search).toEqual({ state: 'active' })
  })

  it('goes back to the first page when the search changes', async () => {
    const many = Array.from({ length: 60 }, (_, i) => person(i + 1, `Person${String(i).padStart(2, '0')}`))
    const { calls, user: u } = await open({ beta: many })
    const region = await listed()
    await u.click(await within(region).findByRole('button', { name: 'Next' }))
    await within(region).findByText('26–50 of 60')
    await u.type(screen.getByLabelText('Search by name, email or firm'), 'person{Enter}')
    await within(region).findByText('1–25 of 60')
    expect(requests(calls).at(-1)?.query).toMatchObject({ q: 'person', offset: '0' })
  })

  it('closes the open person when the list changes, since they may no longer be in it', async () => {
    const { router, user: u } = await open({}, { person: 2 })
    expect(await screen.findByRole('heading', { name: 'Priya' })).toBeInTheDocument()
    await u.click(
      within(await screen.findByRole('group', { name: 'Filter by state' })).getByRole('button', { name: /^Waiting/ }),
    )
    await waitFor(() => expect(router.state.location.search).toEqual({ state: 'waiting' }))
    expect(await within(await panel()).findByText(/Choose a person/)).toBeInTheDocument()
  })

  it('searches by name, address or firm after a pause, with the text trimmed', async () => {
    const { calls, user: u } = await open()
    await listed()
    await screen.findByText('alex@example.test')
    await u.type(screen.getByLabelText('Search by name, email or firm'), '  apex ')
    await waitFor(() => expect(requests(calls).at(-1)?.query['q']).toBe('apex'), { timeout: 3000 })
    await waitFor(() => expect(rowsOf(screen.getByRole('region', { name: 'Beta testers' }))).toHaveLength(2))
  })

  it('does not search for every keystroke', async () => {
    const { calls, user: u } = await open()
    await listed()
    await screen.findByText('alex@example.test')
    const before = requests(calls).length
    await u.type(screen.getByLabelText('Search by name, email or firm'), 'abcdef')
    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(requests(calls)).toHaveLength(before)
  })

  it('says so when nobody has joined', async () => {
    await open({ beta: [] })
    expect(await screen.findByText('Nobody has joined the beta yet.')).toBeInTheDocument()
  })

  it('says nobody matches when a search or a filter finds no one', async () => {
    await open({ beta: [ALEX] }, { q: 'zzz' })
    expect(await screen.findByText('Nobody matches.')).toBeInTheDocument()
  })

  it('shows the server’s own sentence when the list is refused', async () => {
    await open({ listFails: { status: 500, detail: 'The server had a problem. Try again in a moment.' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('The server had a problem.')
  })

  it('pages through more than 25 people, and says where it is', async () => {
    const many = Array.from({ length: 60 }, (_, i) => person(i + 1, `Person${String(i).padStart(2, '0')}`))
    const { calls, user: u } = await open({ beta: many })
    const region = await listed()
    expect(await within(region).findByText('1–25 of 60')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Previous' })).toBeDisabled()
    await u.click(within(region).getByRole('button', { name: 'Next' }))
    expect(await within(region).findByText('26–50 of 60')).toBeInTheDocument()
    expect(requests(calls).at(-1)?.query).toMatchObject({ offset: '25', limit: '25' })
    await u.click(within(region).getByRole('button', { name: 'Next' }))
    expect(await within(region).findByText('51–60 of 60')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('ignores a malformed address instead of trusting it', async () => {
    const { calls } = await open({}, { state: 'deleted', page: 'x', person: '-4', q: 'x'.repeat(300) })
    await listed()
    expect(requests(calls)[0]?.query).toEqual({ q: 'x'.repeat(100), limit: '25', offset: '0' })
  })

  it('keeps the search, the state, the page and the open person in the address, so a view can be shared', async () => {
    const { router } = await open({}, { q: 'apex', state: 'active', person: 2 })
    expect(await screen.findByRole('heading', { name: 'Priya' })).toBeInTheDocument()
    expect(router.state.location.search).toEqual({ q: 'apex', state: 'active', person: 2 })
    expect(screen.getByLabelText('Search by name, email or firm')).toHaveValue('apex')
  })
})

describe('one person', () => {
  it('asks to choose someone before anyone is open', async () => {
    await open()
    expect(await within(await panel()).findByText(/Choose a person/)).toBeInTheDocument()
  })

  it('shows their facts for someone whose 30 days run', async () => {
    const { user: u } = await open()
    await u.click(await screen.findByRole('button', { name: 'Alex' }))
    const side = await panel()
    expect(await within(side).findByRole('heading', { name: 'Alex' })).toBeInTheDocument()
    expect(side).toHaveTextContent('alex@example.test')
    expect(side).toHaveTextContent('Favourite firmTopstep')
    expect(side).toHaveTextContent('JoinedOct 9, 2026')
    expect(side).toHaveTextContent('Sign-up codeUsed')
    expect(side).toHaveTextContent('Code emailSent Oct 9, 2026')
    expect(side).toHaveTextContent('Access endsNov 8, 2026 · 23 days')
    expect(side).toHaveTextContent('Ended emailNot sent yet')
  })

  it('says a code email did not leave, and how many tries failed, and that no firm was given', async () => {
    const { user: u } = await open()
    await u.click(await screen.findByRole('button', { name: 'Dana' }))
    const side = await panel()
    expect(await within(side).findByRole('heading', { name: 'Dana' })).toBeInTheDocument()
    expect(side).toHaveTextContent('Code emailNot sent (2 tries failed)')
    expect(side).toHaveTextContent('Favourite firmNone given')
    expect(side).toHaveTextContent('Sign-up codeExpired')
  })

  it('shows who ran out and who was revoked, with the day the access ended and when the email left', async () => {
    const { user: u } = await open()
    await u.click(await screen.findByRole('button', { name: 'Sam' }))
    const sam = await panel()
    expect(await within(sam).findByRole('heading', { name: 'Sam' })).toBeInTheDocument()
    expect(sam).toHaveTextContent('Access endedOct 1, 2026')
    expect(sam).toHaveTextContent('Ended emailSent Oct 1, 2026')
    await u.click(screen.getByRole('button', { name: 'Chris' }))
    expect(await within(await panel()).findByRole('heading', { name: 'Chris' })).toBeInTheDocument()
    expect(await panel()).toHaveTextContent('Access endedOct 5, 2026') // the day it was revoked, not the day it would have run out
  })

  it('closes the panel when the same person is chosen again', async () => {
    const { user: u } = await open()
    const alex = await screen.findByRole('button', { name: 'Alex' })
    await u.click(alex)
    await within(await panel()).findByRole('heading', { name: 'Alex' })
    await u.click(alex)
    expect(await within(await panel()).findByText(/Choose a person/)).toBeInTheDocument()
  })

  it.each(['Sam', 'Chris'])('offers no action for %s: there is nothing left to end', async (name) => {
    const { user: u } = await open()
    await u.click(await screen.findByRole('button', { name }))
    const side = await panel()
    await within(side).findByRole('heading', { name })
    expect(within(side).queryByRole('button')).not.toBeInTheDocument()
    expect(side).toHaveTextContent('There is nothing left to end for this person.')
  })
})

describe('ending someone’s access', () => {
  async function ask(name: string, button: string, options: AdminStubOptions = {}) {
    const app = await open(options)
    await app.user.click(await screen.findByRole('button', { name }))
    const side = await panel()
    await app.user.click(await within(side).findByRole('button', { name: new RegExp(button) }))
    const dialog = await screen.findByRole('dialog')
    return { ...app, dialog }
  }

  it('says what will happen to a person with an account: now, emailed, journal kept read-only', async () => {
    const { dialog } = await ask('Alex', 'Revoke beta access')
    expect(within(dialog).getByRole('heading', { name: 'Revoke beta access?' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Alex (alex@example.test) will lose access to Roundturn now.')
    expect(dialog).toHaveTextContent('They will be emailed that they need to sign up to continue.')
    expect(dialog).toHaveTextContent('Their journal is kept, read-only.')
  })

  it('needs a reason before it can be sent', async () => {
    const { dialog, calls, user: u } = await ask('Alex', 'Revoke beta access')
    const send = within(dialog).getByRole('button', { name: 'Revoke access and send email' })
    expect(send).toBeDisabled()
    await u.type(within(dialog).getByLabelText(/^Reason/), '   ')
    expect(send).toBeDisabled()
    expect(changes(calls)).toEqual([])
  })

  it('sends the trimmed reason with the CSRF token, closes, says they were emailed, and reads the list again', async () => {
    const { dialog, calls, user: u } = await ask('Alex', 'Revoke beta access')
    const before = requests(calls).length
    await u.type(within(dialog).getByLabelText(/^Reason/), '  Asked to leave the beta.  ')
    await u.click(within(dialog).getByRole('button', { name: 'Revoke access and send email' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(changes(calls)).toEqual([
      {
        method: 'POST',
        path: '/api/admin/v1/beta/1/revoke',
        query: {},
        body: { reason: 'Asked to leave the beta.' },
        csrf: 'tok',
      },
    ])
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Alex: They were emailed that they need to sign up to continue.',
    )
    await waitFor(() => expect(requests(calls).length).toBeGreaterThan(before))
  })

  it('says so when the email did not leave, and that the access has ended all the same', async () => {
    const { dialog, user: u } = await ask('Priya', 'Revoke beta access', { betaNoticeFails: true })
    await u.type(within(dialog).getByLabelText(/^Reason/), 'abuse')
    await u.click(within(dialog).getByRole('button', { name: 'Revoke access and send email' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Priya: The email did not go out. Their access has ended all the same.',
    )
  })

  it('withdraws the code of someone with no account yet, and says nothing is emailed', async () => {
    const { dialog, calls, user: u } = await ask('Jordan', 'Withdraw code')
    expect(within(dialog).getByRole('heading', { name: 'Withdraw this code?' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('has not made an account yet')
    expect(dialog).toHaveTextContent('Nothing is emailed.')
    await u.type(within(dialog).getByLabelText(/^Reason/), 'duplicate')
    await u.click(within(dialog).getByRole('button', { name: 'Withdraw code' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Jordan: They had no account, so only their code was withdrawn. Nothing was emailed.',
    )
    expect(changes(calls)[0]).toMatchObject({ path: '/api/admin/v1/beta/3/revoke', body: { reason: 'duplicate' } })
  })

  it('keeps the dialog open and says what the server said when the change is refused', async () => {
    const { dialog, user: u } = await ask('Alex', 'Revoke beta access', {
      changeFails: { status: 409, detail: 'There is nothing left to end for this person.' },
    })
    await u.type(within(dialog).getByLabelText(/^Reason/), 'again')
    await u.click(within(dialog).getByRole('button', { name: 'Revoke access and send email' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('There is nothing left to end for this person.')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('does nothing when it is cancelled', async () => {
    const { dialog, calls, user: u } = await ask('Alex', 'Revoke beta access')
    await u.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(changes(calls)).toEqual([])
  })

  it('dismisses the note about the last change', async () => {
    const { dialog, user: u } = await ask('Alex', 'Revoke beta access')
    await u.type(within(dialog).getByLabelText(/^Reason/), 'bye')
    await u.click(within(dialog).getByRole('button', { name: 'Revoke access and send email' }))
    const note = await screen.findByRole('status')
    await u.click(within(note).getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('removing a sign-up', () => {
  async function ask(name: string, options: AdminStubOptions = {}) {
    const app = await open(options)
    await app.user.click(await screen.findByRole('button', { name }))
    const side = await panel()
    await app.user.click(await within(side).findByRole('button', { name: 'Remove sign-up' }))
    const dialog = await screen.findByRole('dialog')
    return { ...app, dialog }
  }

  it.each(['Jordan', 'Dana'])('is offered for %s, who has no account', async (name) => {
    const { user: u } = await open()
    await u.click(await screen.findByRole('button', { name }))
    const side = await panel()
    expect(await within(side).findByRole('button', { name: 'Remove sign-up' })).toBeInTheDocument()
  })

  it.each(['Alex', 'Sam', 'Chris'])('is not offered for %s, who has an account', async (name) => {
    const { user: u } = await open()
    await u.click(await screen.findByRole('button', { name }))
    const side = await panel()
    await within(side).findByRole('heading', { name })
    expect(within(side).queryByRole('button', { name: 'Remove sign-up' })).not.toBeInTheDocument()
  })

  it('leaves Dana, whose code ran out, with nothing to end and only the removal', async () => {
    const { user: u } = await open()
    await u.click(await screen.findByRole('button', { name: 'Dana' }))
    const side = await panel()
    await within(side).findByRole('heading', { name: 'Dana' })
    expect(side).toHaveTextContent('There is nothing left to end for this person.')
    expect(
      within(side)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Remove sign-up'])
  })

  it('sits beside withdrawing the code for someone whose code is still good', async () => {
    const { user: u } = await open()
    await u.click(await screen.findByRole('button', { name: 'Jordan' }))
    const side = await panel()
    await within(side).findByRole('heading', { name: 'Jordan' })
    expect(
      within(side)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Withdraw code', 'Remove sign-up'])
  })

  it('says what is deleted, and that nothing is emailed', async () => {
    const { dialog } = await ask('Jordan')
    expect(within(dialog).getByRole('heading', { name: 'Remove this sign-up?' })).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Jordan (jordan@example.test) has not made an account.')
    expect(dialog).toHaveTextContent('every code made for them are deleted for good')
    expect(dialog).toHaveTextContent('Nothing is emailed.')
  })

  it('needs a reason before it can be sent', async () => {
    const { dialog, calls, user: u } = await ask('Jordan')
    const send = within(dialog).getByRole('button', { name: 'Remove sign-up' })
    expect(send).toBeDisabled()
    await u.type(within(dialog).getByLabelText(/^Reason/), '   ')
    expect(send).toBeDisabled()
    expect(changes(calls)).toEqual([])
  })

  it('sends the trimmed reason with the CSRF token, closes, says it is gone, and the person leaves the list', async () => {
    const { dialog, calls, router, user: u } = await ask('Jordan')
    expect(router.state.location.search).toMatchObject({ person: 3 })
    await u.type(within(dialog).getByLabelText(/^Reason/), '  They asked by email to be removed.  ')
    await u.click(within(dialog).getByRole('button', { name: 'Remove sign-up' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(changes(calls)).toEqual([
      {
        method: 'POST',
        path: '/api/admin/v1/beta/3/remove',
        query: {},
        body: { reason: 'They asked by email to be removed.' },
        csrf: 'tok',
      },
    ])
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Jordan: Their sign-up and every code made for them were deleted.',
    )
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Jordan' })).not.toBeInTheDocument())
    expect(await within(await panel()).findByText(/Choose a person/)).toBeInTheDocument()
    expect(router.state.location.search).not.toHaveProperty('person') // the address no longer points at them
  })

  it('keeps the dialog open and says what the server said when it is refused', async () => {
    const { dialog, user: u } = await ask('Jordan', {
      changeFails: { status: 409, detail: 'They have an account. Delete the account instead.' },
    })
    await u.type(within(dialog).getByLabelText(/^Reason/), 'asked')
    await u.click(within(dialog).getByRole('button', { name: 'Remove sign-up' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'They have an account. Delete the account instead.',
    )
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('does nothing when it is cancelled', async () => {
    const { dialog, calls, user: u } = await ask('Jordan')
    await u.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(changes(calls)).toEqual([])
    expect(screen.getByRole('button', { name: 'Jordan' })).toBeInTheDocument()
  })
})

describe('removing a sign-up: the form guards itself as well as the button', () => {
  it('does not send an empty reason even when the form is submitted without the button', async () => {
    const app = await open()
    await app.user.click(await screen.findByRole('button', { name: 'Jordan' }))
    await app.user.click(await within(await panel()).findByRole('button', { name: 'Remove sign-up' }))
    const dialog = await screen.findByRole('dialog')
    await app.user.type(within(dialog).getByLabelText(/^Reason/), '   ')
    fireEvent.submit(dialog.querySelector('form') as HTMLFormElement)
    await new Promise((resolve) => setTimeout(resolve, 50)) // long enough for a request to have been made
    expect(changes(app.calls)).toEqual([])
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
