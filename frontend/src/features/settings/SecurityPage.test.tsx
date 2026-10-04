import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActiveSession } from '../../lib/sessions'
import { json, openApp, stubApi } from '../../test-utils'

afterEach(() => vi.unstubAllGlobals())

const row = (id: string, device: string, extra: Partial<ActiveSession> = {}): ActiveSession => ({
  id,
  current: false,
  device,
  signed_in_at: '2026-10-04T16:05:00Z',
  last_active_at: '2026-10-04T18:30:00Z',
  expires_at: '2027-01-02T16:05:00Z',
  mfa: false,
  ...extra,
})

const LAPTOP = row('s-laptop', 'Chrome on Windows', { current: true, mfa: true })
const PHONE = row('s-phone', 'Safari on iPhone')
const TABLET = row('s-tablet', 'Chrome on Android', { last_active_at: '2026-10-01T09:00:00Z' })

interface Options {
  /** Answer the list with this problem instead. */
  listFails?: boolean
  /** Answer ending one with this problem. */
  endFails?: { status: number; detail: string }
  /** What the recent-activity list says (newest first). Default: nothing yet. */
  activity?: object[]
  /** Answer the recent-activity list with a problem. */
  activityFails?: boolean
}

/** The app's usual calls, plus an in-memory `/api/v1/sessions` that behaves like the server's. */
function stubSessions(initial: ActiveSession[], options: Options = {}) {
  stubApi(options.activity ? { activity: options.activity } : {})
  const inner = globalThis.fetch
  const state = { rows: [...initial] }
  const calls: { method: string; path: string; csrf: string | null }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      if (url.pathname === '/api/v1/activity') {
        calls.push({ method: input.method, path: url.pathname, csrf: input.headers.get('x-csrf-token') })
        if (options.activityFails) return json({ title: 'Internal Server Error', status: 500, detail: null }, 500)
        return inner(input)
      }
      if (!url.pathname.startsWith('/api/v1/sessions')) return inner(input)
      calls.push({ method: input.method, path: url.pathname, csrf: input.headers.get('x-csrf-token') })
      const answer = (ended: number) => json({ sessions: state.rows, ended })
      if (input.method === 'GET') {
        if (options.listFails) return json({ title: 'Internal Server Error', status: 500, detail: null }, 500)
        return answer(0)
      }
      if (url.pathname === '/api/v1/sessions') {
        const before = state.rows.length
        state.rows = state.rows.filter((r) => r.current)
        return answer(before - state.rows.length)
      }
      if (options.endFails) return json({ title: 'Not Found', ...options.endFails }, options.endFails.status)
      const id = url.pathname.split('/').pop()
      state.rows = state.rows.filter((r) => r.id !== id)
      return answer(1)
    }),
  )
  return calls
}

async function open() {
  const app = openApp('/settings/security')
  await screen.findByRole('heading', { name: 'Security' })
  return { ...app, user: userEvent.setup() }
}

const list = () => screen.findByRole('region', { name: 'Where you are signed in' })

describe('the security section', () => {
  it('lists where the account is signed in, marks this device, and times them in the account time zone', async () => {
    stubSessions([LAPTOP, PHONE, TABLET])
    await open()
    const items = within(await list()).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('Chrome on Windows')
    expect(items[0]).toHaveTextContent('This device')
    expect(items[0]).toHaveTextContent('Two-step verification')
    // 16:05 UTC is 09:05 in the account's America/Los_Angeles; the latest use was 18:30 UTC, 11:30 there.
    expect(items[0]).toHaveTextContent('Signed in Oct 4 · 09:05')
    expect(items[0]).toHaveTextContent('Last active Oct 4 · 11:30')
    expect(items[1]).not.toHaveTextContent('This device')
    expect(items[1]).not.toHaveTextContent('Two-step verification')
  })

  it('offers Sign out on the other places only, never on this device', async () => {
    stubSessions([LAPTOP, PHONE])
    await open()
    await list()
    expect(screen.getByRole('button', { name: 'Sign out Safari on iPhone' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign out Chrome on Windows' })).not.toBeInTheDocument()
    expect(screen.getByText(/use Sign out in the account menu/)).toBeInTheDocument()
  })

  it('signs one place out with the CSRF token, and shows the list as it is afterwards', async () => {
    const calls = stubSessions([LAPTOP, PHONE, TABLET])
    const { user } = await open()
    await list()
    await user.click(screen.getByRole('button', { name: 'Sign out Safari on iPhone' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Signed out of that device.')
    const items = within(await list()).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining('Chrome on Windows'),
      expect.stringContaining('Chrome on Android'),
    ])
    const ended = calls.filter((c) => c.method === 'DELETE')
    expect(ended).toEqual([{ method: 'DELETE', path: '/api/v1/sessions/s-phone', csrf: 'tok' }])
  })

  it('signs out everywhere else in one step and says how many places it was', async () => {
    const calls = stubSessions([LAPTOP, PHONE, TABLET])
    const { user } = await open()
    await list()
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere else' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Signed out of 2 other places.')
    await waitFor(() =>
      expect(
        within(screen.getByRole('region', { name: 'Where you are signed in' })).getAllByRole('listitem'),
      ).toHaveLength(1),
    )
    expect(screen.getByRole('button', { name: 'Sign out everywhere else' })).toBeDisabled() // nothing left to end
    expect(calls.filter((c) => c.method === 'DELETE')).toEqual([
      { method: 'DELETE', path: '/api/v1/sessions', csrf: 'tok' },
    ])
  })

  it('has nothing to sign out of when this is the only place', async () => {
    const calls = stubSessions([LAPTOP])
    await open()
    await list()
    expect(screen.getByRole('button', { name: 'Sign out everywhere else' })).toBeDisabled()
    expect(calls.every((c) => c.method === 'GET')).toBe(true)
  })

  it("shows the server's own sentence when ending one is refused, and keeps the list", async () => {
    stubSessions([LAPTOP, PHONE], { endFails: { status: 404, detail: 'That session does not exist.' } })
    const { user } = await open()
    await list()
    await user.click(screen.getByRole('button', { name: 'Sign out Safari on iPhone' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That session does not exist.')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(within(await list()).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Sign out Safari on iPhone' })).toBeEnabled() // and can be tried again
  })

  it('says so when the list cannot be loaded', async () => {
    stubSessions([LAPTOP], { listFails: true })
    await open()
    expect(await screen.findByRole('alert')).toHaveTextContent('The server had a problem. Try again in a moment.')
    expect(screen.queryByRole('region', { name: 'Where you are signed in' })).not.toBeInTheDocument()
  })

  it('is one of the settings sections', async () => {
    stubSessions([LAPTOP])
    await open()
    const nav = screen.getByRole('navigation', { name: 'Settings sections' })
    expect(within(nav).getByRole('link', { name: 'Security' })).toHaveAttribute('aria-current', 'page')
  })
})

const EVENTS = [
  { at: '2026-10-04T18:30:00Z', kind: 'sign_out_everywhere', ended: 2, mfa: null },
  { at: '2026-10-04T16:05:00Z', kind: 'sign_in', ended: null, mfa: true },
  { at: '2026-10-03T08:00:00Z', kind: 'sign_in_refused', ended: null, mfa: null },
  { at: '2026-10-02T08:00:00Z', kind: 'export', ended: null, mfa: null },
]

const activity = () => screen.findByRole('region', { name: 'Recent activity' })

describe('recent activity', () => {
  it('lists what has happened to the account in words, newest first, timed in the account time zone', async () => {
    stubSessions([LAPTOP], { activity: EVENTS })
    await open()
    const items = within(await activity()).getAllByRole('listitem')
    expect(items.map((item) => item.textContent)).toEqual([
      'Signed out of 2 other placesOct 4 · 11:30',
      'Signed in with two-step verificationOct 4 · 09:05',
      'A sign-in to your account was refusedOct 3 · 01:00',
      'Downloaded your dataOct 2 · 01:00',
    ])
  })

  it('gives each time as a machine-readable date as well', async () => {
    stubSessions([LAPTOP], { activity: EVENTS })
    await open()
    const times = within(await activity()).getAllByText(/Oct/)
    expect(times[1]).toHaveAttribute('datetime', '2026-10-04T16:05:00Z')
  })

  it('says nothing yet when there is nothing', async () => {
    stubSessions([LAPTOP])
    await open()
    expect(within(await activity()).getByText('Nothing yet.')).toBeInTheDocument()
    expect(within(await activity()).queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('says so, in the card, when the list cannot be loaded, and the sign-ins above still show', async () => {
    stubSessions([LAPTOP, PHONE], { activityFails: true })
    await open()
    expect(await within(await activity()).findByRole('alert')).toHaveTextContent(
      'The server had a problem. Try again in a moment.',
    )
    expect(within(await list()).getAllByRole('listitem')).toHaveLength(2)
  })

  it('marks a refused sign-in so it stands out, and no other row', async () => {
    stubSessions([LAPTOP], { activity: EVENTS })
    await open()
    const items = within(await activity()).getAllByRole('listitem')
    const warned = items.filter((item) => item.querySelector('.text-warn'))
    expect(warned).toHaveLength(1)
    expect(warned[0]).toHaveTextContent('A sign-in to your account was refused')
  })

  it('says only the person can see it, and what to do when something is not theirs', async () => {
    stubSessions([LAPTOP])
    await open()
    expect(within(await activity()).getByText(/Only you can see this list/)).toBeInTheDocument()
    expect(within(await activity()).getByText(/sign out everywhere else/)).toBeInTheDocument()
  })

  it('asks for it once on opening, and again after signing out everywhere else, since that is an event too', async () => {
    const calls = stubSessions([LAPTOP, PHONE], { activity: EVENTS })
    const { user } = await open()
    await activity()
    expect(calls.filter((c) => c.path === '/api/v1/activity')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Sign out everywhere else' }))
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/v1/activity')).toHaveLength(2))
  })

  it('asks again after signing one place out too', async () => {
    const calls = stubSessions([LAPTOP, PHONE], { activity: EVENTS })
    const { user } = await open()
    await activity()
    await user.click(screen.getByRole('button', { name: 'Sign out Safari on iPhone' }))
    await waitFor(() => expect(calls.filter((c) => c.path === '/api/v1/activity')).toHaveLength(2))
  })

  it('only reads: no change is sent for it, and no CSRF token is needed', async () => {
    const calls = stubSessions([LAPTOP], { activity: EVENTS })
    await open()
    await activity()
    expect(calls.filter((c) => c.path === '/api/v1/activity').map((c) => c.method)).toEqual(['GET'])
  })
})
