import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Decimal from 'decimal.js'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.unstubAllGlobals()
  document.documentElement.removeAttribute('data-theme')
})

/** Opens the log at a URL. `waitForTable` is off for a search that finds nothing, where there is no table to wait for. */
async function openTrades(search: Record<string, string | number> = {}, waitForTable = true) {
  stubApi()
  const { router } = openApp('/trades', search)
  const table = waitForTable ? await screen.findByRole('table') : (null as unknown as HTMLElement)
  return { table, router, user: userEvent.setup() }
}

const bodyRows = (table: HTMLElement) => within(table).getAllByRole('row').slice(1)

/** Column 9 is Net P&L; the typographic minus becomes a hyphen so the text reads as a number. */
const pnlOf = (row: HTMLElement) =>
  new Decimal(
    (within(row).getAllByRole('cell')[8]?.textContent ?? '0').replace('−', '-').replace(/[$,+]/g, ''),
  ).toNumber()

describe('the trade log', () => {
  it('lists the newest twelve trades first, with the totals for the whole selection', async () => {
    const { table } = await openTrades()
    expect(bodyRows(table)).toHaveLength(12)
    expect(screen.getByText('1 to 12 of 132')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Trade log' })).toHaveTextContent('132 trades')
    expect(within(table).getByRole('columnheader', { name: /Date/ })).toHaveAttribute('aria-sort', 'descending')
    expect(within(table).getByRole('columnheader', { name: 'Tags' })).not.toHaveAttribute('aria-sort')
  })

  it('shows each trade in the viewer’s time zone, with a signed result and a side badge', async () => {
    const { table } = await openTrades()
    const first = bodyRows(table)[0] as HTMLElement
    const cells = within(first).getAllByRole('cell')
    expect(cells[0]?.textContent).toMatch(/^[A-Z][a-z]{2} \d{1,2} · \d{2}:\d{2}$/)
    expect(cells[3]?.textContent).toMatch(/^(Long|Short)$/)
    expect(cells[8]?.textContent).toMatch(/^([+−]\$[\d,]+\.\d{2}|\$0\.00)$/)
  })

  it('sorts a column when its header is clicked, and the URL says so', async () => {
    const { table, router, user } = await openTrades()
    await user.click(within(table).getByRole('button', { name: 'Net P&L' }))

    await waitFor(() => expect(router.state.location.search).toMatchObject({ sort: '-net_pnl' }))
    await waitFor(() =>
      expect(within(table).getByRole('columnheader', { name: /Net P&L/ })).toHaveAttribute('aria-sort', 'descending'),
    )
    const biggestFirst = bodyRows(table).map(pnlOf)
    expect(biggestFirst).toEqual([...biggestFirst].sort((a, b) => b - a))

    await user.click(within(table).getByRole('button', { name: /Net P&L/ }))
    await waitFor(() => expect(router.state.location.search).toMatchObject({ sort: 'net_pnl' }))
    await waitFor(() => {
      const smallestFirst = bodyRows(table).map(pnlOf)
      expect(smallestFirst).toEqual([...smallestFirst].sort((a, b) => a - b))
    })
  })

  it('opens at the sort, page and size named in the URL', async () => {
    const { table } = await openTrades({ sort: 'symbol', page: 2, per_page: 25 })
    expect(screen.getByText('26 to 50 of 132')).toBeInTheDocument()
    expect(bodyRows(table)).toHaveLength(25)
    expect(within(table).getByRole('columnheader', { name: /Symbol/ })).toHaveAttribute('aria-sort', 'ascending')
    expect(screen.getByText('2 / 6')).toBeInTheDocument()
  })

  it('moves between pages and stops at both ends', async () => {
    const { user, router } = await openTrades()
    const previous = screen.getByRole('button', { name: 'Previous page' })
    const next = screen.getByRole('button', { name: 'Next page' })
    expect(previous).toBeDisabled()

    await user.click(next)
    await waitFor(() => expect(screen.getByText('13 to 24 of 132')).toBeInTheDocument())
    expect(router.state.location.search).toMatchObject({ page: 2 })
    expect(previous).toBeEnabled()

    await user.click(previous)
    await waitFor(() => expect(screen.getByText('1 to 12 of 132')).toBeInTheDocument())
  })

  it('reaches the last, short page, where next is disabled', async () => {
    await openTrades({ page: 11 })
    await waitFor(() => expect(screen.getByText('121 to 132 of 132')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    expect(screen.getByText('11 / 11')).toBeInTheDocument()
  })

  it('changes the page size and starts again from the first page', async () => {
    const { table, router, user } = await openTrades({ page: 3 })
    await waitFor(() => expect(screen.getByText('25 to 36 of 132')).toBeInTheDocument())
    await user.selectOptions(screen.getByRole('combobox', { name: 'Per page' }), '50')
    await waitFor(() => expect(screen.getByText('1 to 50 of 132')).toBeInTheDocument())
    expect(bodyRows(table)).toHaveLength(50)
    expect(router.state.location.search).toMatchObject({ per_page: 50, page: 1 })
  })

  it('searches after a short pause and goes back to the first page', async () => {
    const { user, router } = await openTrades({ page: 4 })
    const box = screen.getByRole('searchbox', { name: 'Search trades' })
    await user.type(box, 'es')

    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'es', page: 1 }))
    const region = screen.getByRole('region', { name: 'Trade log' })
    await waitFor(() => expect(region).not.toHaveTextContent('132 trades'))
    expect(screen.getByText(/^1 to \d+ of \d+$/)).toBeInTheDocument()
  })

  it('follows the URL when the search is cleared from outside (the back button, a reset)', async () => {
    const { user, router } = await openTrades({ q: 'nq' })
    const box = screen.getByRole('searchbox', { name: 'Search trades' })
    expect(box).toHaveValue('nq')
    await user.clear(box)
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('q'))
    await waitFor(() => expect(screen.getByText('1 to 12 of 132')).toBeInTheDocument())
  })

  it('says so, plainly, when nothing matches', async () => {
    await openTrades({ q: 'zzzz-no-such-thing' }, false)
    const empty = await screen.findByText('No trades match')
    expect(empty.closest('[role="status"]')).toHaveTextContent('Change the search or the filters above.')
    expect(screen.getByText('0 trades')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('goes back to the first page when a filter changes, keeping the search and the sort', async () => {
    const { user, router } = await openTrades({ page: 3, sort: 'symbol', q: 'n' })
    const range = await screen.findByRole('group', { name: 'Date range' })
    await user.click(within(range).getByRole('button', { name: '7D' }))
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ preset: '7d', page: 1, sort: 'symbol', q: 'n' }),
    )
  })

  it('links each trade to its own page, keeping the view', async () => {
    const { table } = await openTrades({ sort: 'symbol' })
    const link = within(bodyRows(table)[0] as HTMLElement).getByRole('link')
    expect(link.getAttribute('href')).toMatch(/^\/trades\/\d+\?.*sort=symbol/)
  })
})
