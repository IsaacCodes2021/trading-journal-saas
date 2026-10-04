import { waitFor, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { go } from './lib/auth'
import { json, openApp as open, stubApi } from './test-utils'

afterEach(() => {
  vi.unstubAllGlobals()
  document.documentElement.removeAttribute('data-theme')
})

describe('the app shell', () => {
  it('reads the date range from the URL and shows who is signed in, from the account menu', async () => {
    stubApi()
    open('/', { preset: '7d' })

    const range = await screen.findByRole('group', { name: 'Date range' })
    expect(within(range).getByRole('button', { name: '7D' })).toHaveAttribute('aria-pressed', 'true') // from the URL
    expect(within(range).getByRole('button', { name: '30D' })).toHaveAttribute('aria-pressed', 'false')

    const user = userEvent.setup()
    expect(screen.queryByTestId('who')).not.toBeInTheDocument() // tucked away until asked for
    await user.click(screen.getByRole('button', { name: 'Account menu' }))
    expect(screen.getByTestId('who')).toHaveTextContent('alice@example.test')
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('names the product, lists the four screens, and carries the filters from one to the next', async () => {
    stubApi()
    open('/', { preset: '90d', direction: 'long' })

    await screen.findByRole('group', { name: 'Date range' })
    expect(screen.getByText('Roundturn')).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(
      within(nav)
        .getAllByRole('link')
        .map((a) => a.textContent),
    ).toEqual(['Dashboard', 'Trades', 'Import', 'Settings'])
    const trades = within(nav).getByRole('link', { name: 'Trades' })
    expect(trades.getAttribute('href')).toContain('preset=90d')
    expect(trades.getAttribute('href')).toContain('direction=long')
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
  })

  it('says plainly that the numbers are sample data', async () => {
    stubApi()
    open('/')
    const note = await screen.findByRole('note')
    expect(note).toHaveTextContent('Sample data.')
    expect(note).toHaveTextContent('made up')
  })

  it('shows the plan, and the days left on one that is running out', async () => {
    const soon = new Date(Date.now() + 9 * 86_400_000).toISOString()
    stubApi({ plan: { effective: 'pro', source: 'grant', expires_at: soon } })
    open('/')
    expect(await screen.findByText('Pro · 9 days left')).toBeInTheDocument()
  })

  it('shows a free plan with no countdown', async () => {
    stubApi()
    open('/')
    expect(await screen.findByText('Free')).toBeInTheDocument()
    expect(screen.queryByText(/days? left/)).not.toBeInTheDocument()
  })

  it('applies the account theme, switches it at once, and saves the choice', async () => {
    const saved = stubApi({ theme: 'light' })
    open('/')
    const user = userEvent.setup()

    await waitFor(() => expect(document.documentElement.dataset['theme']).toBe('light'))
    await user.click(await screen.findByRole('button', { name: 'Switch to dark theme' }))

    expect(document.documentElement.dataset['theme']).toBe('dark')
    await waitFor(() => expect(saved).toEqual([{ theme: 'dark' }]))
    expect(await screen.findByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
  })

  it('puts the theme back when saving it fails', async () => {
    stubApi({ theme: 'dark', saveFails: true })
    open('/')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Switch to light theme' }))
    await waitFor(() => expect(document.documentElement.dataset['theme']).toBe('dark'))
  })

  it('writes a filter change to the URL', async () => {
    stubApi()
    const { router } = open('/', { preset: '7d' })
    const user = userEvent.setup()

    const range = await screen.findByRole('group', { name: 'Date range' })
    await user.click(within(range).getByRole('button', { name: '90D' }))

    await waitFor(() => expect(router.state.location.search).toMatchObject({ preset: '90d' }))
    expect(within(range).getByRole('button', { name: '90D' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('opens settings at the preferences section', async () => {
    stubApi()
    open('/settings')
    expect(await screen.findByRole('heading', { name: 'Preferences' })).toBeInTheDocument()
  })

  it('opens the import page', async () => {
    stubApi()
    open('/import')
    expect(await screen.findByRole('heading', { name: 'Import trades' })).toBeInTheDocument()
  })
})

describe('signing out', () => {
  const PROVIDER_LOGOUT = 'https://idp.example/logout?redirect=https%3A%2F%2Fapp.example%2F'

  /** The app's own calls are answered as usual; the sign-out call (a plain fetch of /auth/logout) gets `answer`. */
  function stubWithLogout(answer: () => Response | Promise<Response>) {
    stubApi()
    const inner = globalThis.fetch
    const logout = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => answer())
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: Request | string, init?: RequestInit) =>
        input === '/auth/logout' ? logout(input, init) : inner(input as Request),
      ),
    )
    return logout
  }

  async function clickSignOut() {
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Account menu' }))
    await user.click(screen.getByRole('button', { name: 'Sign out' }))
  }

  it('ends the session with the CSRF token, then follows the redirect the server gives', async () => {
    const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
    const logout = stubWithLogout(() => json({ redirect: PROVIDER_LOGOUT }))
    open('/')
    await clickSignOut()
    await waitFor(() => expect(leave).toHaveBeenCalledExactlyOnceWith(PROVIDER_LOGOUT))
    expect(logout).toHaveBeenCalledExactlyOnceWith('/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRF-Token': 'tok' },
    })
    leave.mockRestore()
  })

  it('goes to the sign-in page, and not through sign-in, when the session was already over', async () => {
    const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
    stubWithLogout(() => json({ title: 'Unauthorized', status: 401 }, 401))
    open('/')
    await clickSignOut()
    await waitFor(() => expect(leave).toHaveBeenCalledExactlyOnceWith('/invite'))
    leave.mockRestore()
  })

  it('says so, stays where it is, and lets the person try again when the server could not sign them out', async () => {
    const leave = vi.spyOn(go, 'to').mockImplementation(() => undefined)
    const logout = stubWithLogout(() => json({ title: 'Internal Server Error', status: 500 }, 500))
    open('/')
    await clickSignOut()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not sign you out. Try again.')
    expect(leave).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeEnabled()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Sign out' }))
    await waitFor(() => expect(logout).toHaveBeenCalledTimes(2))
    leave.mockRestore()
  })
})
