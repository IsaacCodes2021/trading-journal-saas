import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SUPPORT_ADDRESS } from '../../components/RouteError'
import { MAX_IMAGES_PER_TRADE, MAX_IMAGE_MB } from '../../lib/limits'
import { MAX_CSV_MB, REQUIRED_COLUMNS } from '../../lib/tradovate'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function openHelp() {
  stubApi()
  const app = openApp('/help')
  await screen.findByRole('region', { name: 'Getting started' })
  return { ...app, user: userEvent.setup({ applyAccept: false }) }
}

const section = (name: string) => screen.getByRole('region', { name })

describe('the page', () => {
  it('is a page with a heading and a section for each kind of question', async () => {
    await openHelp()
    expect(screen.getByRole('heading', { level: 1, name: 'Help' })).toBeInTheDocument()
    expect(screen.getAllByRole('region').map((r) => r.getAttribute('aria-labelledby'))).toEqual([
      'help-start',
      'help-numbers',
      'help-trades',
      'help-filters',
      'help-data',
      'help-problem',
    ])
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([
      'Getting started',
      'How the numbers are worked out',
      'Notes, tags and screenshots',
      'Filters',
      'Your data',
      'When something goes wrong',
    ])
  })

  it('gives the support address as a mail link, twice: at the top and where something has gone wrong', async () => {
    await openHelp()
    const links = screen.getAllByRole('link', { name: SUPPORT_ADDRESS })
    expect(links).toHaveLength(2)
    for (const link of links) expect(link).toHaveAttribute('href', `mailto:${SUPPORT_ADDRESS}`)
  })

  it('says it will never ask for a password or a code', async () => {
    await openHelp()
    expect(within(section('When something goes wrong')).getByText(/we will never ask for one/)).toBeInTheDocument()
  })
})

describe('getting started', () => {
  it('walks through the four steps and links to the places they happen', async () => {
    await openHelp()
    const start = section('Getting started')
    expect(within(start).getAllByRole('listitem')).toHaveLength(4)
    expect(within(start).getByRole('link', { name: /Preferences/ })).toHaveAttribute('href', '/settings/preferences')
    expect(within(start).getByRole('link', { name: /Prop accounts/ })).toHaveAttribute('href', '/settings/accounts')
    expect(within(start).getByRole('link', { name: 'Import' })).toHaveAttribute('href', '/import')
  })

  it('names the largest file the server takes, from the same number the import page uses', async () => {
    await openHelp()
    expect(section('Getting started')).toHaveTextContent(`up to ${MAX_CSV_MB} MB`)
  })

  it('lists every required column, in order, behind a disclosure', async () => {
    const { user } = await openHelp()
    const summary = screen.getByText('The columns the file must have')
    await user.click(summary)
    const codes = [...(summary.closest('details') as HTMLElement).querySelectorAll('code')].map((c) => c.textContent)
    expect(codes).toEqual([...REQUIRED_COLUMNS])
  })

  it('does not give a menu path in Tradovate, since those change between versions', async () => {
    await openHelp()
    expect(section('Getting started')).toHaveTextContent('rather than a fixed path')
  })
})

describe('what it promises', () => {
  it('says what a win, a loss, the win rate, the profit factor and the expectancy are', async () => {
    await openHelp()
    const numbers = section('How the numbers are worked out')
    for (const words of [
      'above zero',
      'exactly zero is breakeven',
      'wins divided by wins plus losses',
      'total won divided by the total lost',
      'net result divided by the number of trades',
      '18:00 New York time',
    ])
      expect(numbers, words).toHaveTextContent(words)
  })

  it('says the prop-firm rules are for information and may differ from the firm’s own', async () => {
    await openHelp()
    expect(section('How the numbers are worked out')).toHaveTextContent('may differ from your firm')
  })

  it('names the screenshot limits from the numbers the trade page uses', async () => {
    await openHelp()
    const trades = section('Notes, tags and screenshots')
    expect(trades).toHaveTextContent(`up to ${MAX_IMAGE_MB} MB each`)
    expect(trades).toHaveTextContent(`a trade can hold ${MAX_IMAGES_PER_TRADE}`)
    expect(trades).toHaveTextContent('SVG')
  })

  it('says that deletion takes seven days and signing in again cancels it, and that operators cannot see content', async () => {
    await openHelp()
    const data = section('Your data')
    expect(data).toHaveTextContent('seven days later')
    expect(data).toHaveTextContent('signing in again before then cancels it')
    expect(data).toHaveTextContent('never a trade, a note or a screenshot')
    expect(within(data).getByRole('link', { name: /Data & privacy/ })).toHaveAttribute('href', '/settings/privacy')
  })

  it('links the calendar and the reports', async () => {
    await openHelp()
    const trades = section('Notes, tags and screenshots')
    expect(within(trades).getByRole('link', { name: 'Calendar' })).toHaveAttribute('href', '/calendar')
    expect(within(trades).getByRole('link', { name: 'Reports' })).toHaveAttribute('href', '/reports')
  })
})

describe('getting to it', () => {
  it('is in the account menu for everyone, and opens the page', async () => {
    stubApi()
    const { router } = openApp('/settings/preferences')
    const user = userEvent.setup({ applyAccept: false })
    await user.click(await screen.findByRole('button', { name: 'Account menu' }))
    const link = await screen.findByRole('link', { name: 'Help' })
    expect(link).toHaveAttribute('href', '/help')
    await user.click(link)
    expect(await screen.findByRole('region', { name: 'Getting started' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/help')
  })

  it('needs no filters in its address and keeps the main navigation', async () => {
    await openHelp()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getAllByRole('link')).toHaveLength(7)
  })
})
