import { waitFor, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { openApp as open, stubApi } from './test-utils'

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
