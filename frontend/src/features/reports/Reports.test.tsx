import Decimal from 'decimal.js'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SAMPLE_TRADES } from '../../lib/demo/generate'
import { resetDemoJournal } from '../../lib/demo/journal'
import { demoSource } from '../../lib/demo/source'
import { formatPnl } from '../../lib/format'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoJournal()
})

const netOf = (trades: readonly { net_pnl: string }[]) =>
  trades.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2)

const ROOTS = [...new Set(SAMPLE_TRADES.map((t) => t.symbol_root))]
const byRoot = (root: string) => SAMPLE_TRADES.filter((t) => t.symbol_root === root)

async function openReports(search: Record<string, string> = {}, { expectTable = true } = {}) {
  stubApi()
  const app = openApp('/reports', { preset: 'all', ...search })
  if (expectTable) await screen.findByRole('table')
  return { ...app, user: userEvent.setup({ applyAccept: false }) }
}

const bodyRows = () => within(screen.getByRole('table').querySelector('tbody') as HTMLElement).getAllByRole('row')
const rowOf = (name: string) =>
  bodyRows().find((r) => within(r).getByRole('rowheader').textContent?.startsWith(name)) as HTMLElement
const pressed = () =>
  within(screen.getByRole('group', { name: 'Break down by' }))
    .getAllByRole('button')
    .filter((b) => b.getAttribute('aria-pressed') === 'true')
    .map((b) => b.textContent)

describe('the page', () => {
  it('is reached from the navigation and has a heading', async () => {
    await openReports()
    expect(screen.getByRole('heading', { level: 1, name: 'Reports' })).toBeInTheDocument()
    expect(
      within(screen.getByRole('navigation', { name: /main|primary/i })).getByRole('link', { name: 'Reports' }),
    ).toBeInTheDocument()
  })

  it('offers the six breakdowns, and the first one, contract, is the one shown', async () => {
    await openReports()
    const group = screen.getByRole('group', { name: 'Break down by' })
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Contract', 'Weekday', 'Time of day', 'Hold time', 'Tag', 'Account'])
    expect(pressed()).toEqual(['Contract'])
  })

  it('names the columns', async () => {
    await openReports()
    expect(
      within(screen.getByRole('table'))
        .getAllByRole('columnheader')
        .map((h) => h.textContent),
    ).toEqual(['Contract', 'Trades', 'Win rate', 'Expectancy', 'Profit factor', 'Avg win', 'Avg loss', 'Net P&L'])
  })
})

describe('by contract', () => {
  it('has a row for each contract traded with its exact count and result, best result first', async () => {
    await openReports()
    const rows = bodyRows()
    expect(rows).toHaveLength(ROOTS.length)
    for (const root of ROOTS) {
      const cells = within(rowOf(root)).getAllByRole('cell')
      expect(cells[0]).toHaveTextContent(String(byRoot(root).length)) // Trades
      expect(rowOf(root)).toHaveTextContent(formatPnl(netOf(byRoot(root))))
    }
    const order = rows.map((r) => within(r).getByRole('rowheader').textContent)
    const expected = [...ROOTS].sort(
      (a, b) => new Decimal(netOf(byRoot(b))).comparedTo(netOf(byRoot(a))) || a.localeCompare(b),
    )
    expect(order).toEqual(expected)
  })

  it('ends with a row for all trades that is the whole journal’s count and result', async () => {
    await openReports()
    const total = within(screen.getByRole('table').querySelector('tfoot') as HTMLElement).getByRole('row')
    expect(within(total).getByRole('rowheader')).toHaveTextContent('All trades')
    expect(total).toHaveTextContent(String(SAMPLE_TRADES.length))
    expect(total).toHaveTextContent(formatPnl(netOf(SAMPLE_TRADES)))
  })

  it('links each contract to the trade log filtered to it, keeping the other filters', async () => {
    const { router } = await openReports({ direction: 'long' })
    const root = ROOTS[0] as string
    const link = within(rowOf(root)).getByRole('link', { name: root })
    const href = link.getAttribute('href') ?? ''
    const url = new URL(href, 'http://app.test')
    expect(url.pathname).toBe('/trades')
    expect(JSON.parse(url.searchParams.get('symbol_roots') ?? 'null')).toEqual([root]) // the router writes a list as JSON
    expect(url.searchParams.get('direction')).toBe('long')
    expect(url.searchParams.get('preset')).toBe('all')
    expect(router.state.location.pathname).toBe('/reports')
  })

  it('draws the biggest result’s bar the whole width and the others less, green for a win and red for a loss', async () => {
    await openReports()
    const bars = [...document.querySelectorAll<HTMLElement>('tbody [style*="width"]')]
    const widths = bars.map((b) => Number.parseFloat(b.style.width))
    expect(Math.max(...widths)).toBe(100)
    expect(widths.every((w) => w > 0 && w <= 100)).toBe(true)
    for (const root of ROOTS) {
      const bar = rowOf(root).querySelector<HTMLElement>('[style*="width"]')
      const net = new Decimal(netOf(byRoot(root)))
      expect(bar?.className).toContain(net.isNegative() ? 'bg-neg' : 'bg-pos')
    }
  })
})

describe('choosing another breakdown', () => {
  it('shows it, presses its button, and puts the choice and the filters in the address', async () => {
    const { user, router } = await openReports({ direction: 'long' })
    await user.click(screen.getByRole('button', { name: 'Weekday' }))
    await waitFor(() => expect(pressed()).toEqual(['Weekday']))
    expect(router.state.location.search).toMatchObject({ by: 'weekday', direction: 'long', preset: 'all' })
    expect(screen.getByRole('columnheader', { name: 'Weekday' })).toBeInTheDocument()
  })

  it('lists all seven weekdays, Monday first, and dims the days with no trades', async () => {
    const { user } = await openReports()
    await user.click(screen.getByRole('button', { name: 'Weekday' }))
    await waitFor(() => expect(pressed()).toEqual(['Weekday']))
    const names = bodyRows().map((r) => within(r).getByRole('rowheader').textContent)
    expect(names).toEqual(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])
    const saturday = rowOf('Saturday')
    expect(saturday).toHaveClass('text-muted')
    expect(saturday).toHaveTextContent('—')
    expect(saturday).not.toHaveTextContent('$0.00') // no trades is not a result of nothing
    expect(rowOf('Monday')).not.toHaveClass('text-muted')
  })

  it('draws no bar for a weekday with no trades, and a bar for one with', async () => {
    const { user } = await openReports()
    await user.click(screen.getByRole('button', { name: 'Weekday' }))
    await waitFor(() => expect(pressed()).toEqual(['Weekday']))
    expect(rowOf('Saturday').querySelector('[style*="width"]')).toBeNull()
    expect(rowOf('Monday').querySelector('[style*="width"]')).not.toBeNull()
  })

  it('draws a losing group’s bar red and a winning one’s green', async () => {
    const { user } = await openReports()
    await user.click(screen.getByRole('button', { name: 'Tag' }))
    await waitFor(() => expect(pressed()).toEqual(['Tag']))
    const data = await demoSource.report('tag', { preset: 'all' })
    const losing = data.groups.filter((g) => Number(g.summary.net_pnl) < 0)
    const winning = data.groups.filter((g) => Number(g.summary.net_pnl) > 0)
    expect(losing.length).toBeGreaterThan(0) // the sample has tags that lose money, so this checks something
    for (const g of losing) expect(rowOf(g.label).querySelector('[style*="width"]')).toHaveClass('bg-neg')
    for (const g of winning) expect(rowOf(g.label).querySelector('[style*="width"]')).toHaveClass('bg-pos')
  })

  it('names the category of each tag under it', async () => {
    const { user } = await openReports()
    await user.click(screen.getByRole('button', { name: 'Tag' }))
    await waitFor(() => expect(pressed()).toEqual(['Tag']))
    expect(rowOf('A+ setup')).toHaveTextContent('Setup')
    expect(rowOf('FOMO')).toHaveTextContent('Mistake')
    expect(rowOf('Untagged').querySelector('.text-xs')).toBeNull()
  })

  it('keeps the chosen breakdown when a filter is changed', async () => {
    const { user, router } = await openReports({ by: 'weekday' })
    await user.click(within(screen.getByRole('group', { name: 'Date range' })).getByRole('button', { name: '7D' }))
    await waitFor(() => expect(router.state.location.search).toMatchObject({ preset: '7d' }))
    expect(router.state.location.search).toMatchObject({ by: 'weekday' })
    expect(pressed()).toEqual(['Weekday'])
  })

  it('says the hours are in the person’s own timezone, and names it', async () => {
    const { user } = await openReports()
    await user.click(screen.getByRole('button', { name: 'Time of day' }))
    await waitFor(() => expect(pressed()).toEqual(['Time of day']))
    expect(screen.getByText(/in your timezone\. \(America\/Los_Angeles\)/, { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Hour of entry' })).toBeInTheDocument()
    expect(within(bodyRows()[0] as HTMLElement).getByRole('rowheader').textContent).toMatch(/^\d\d:00$/)
  })

  it('lists every hold step, and says what a tag report counts', async () => {
    const { user } = await openReports()
    await user.click(screen.getByRole('button', { name: 'Hold time' }))
    await waitFor(() => expect(pressed()).toEqual(['Hold time']))
    expect(bodyRows()).toHaveLength(7)
    await user.click(screen.getByRole('button', { name: 'Tag' }))
    await waitFor(() => expect(pressed()).toEqual(['Tag']))
    expect(screen.getByText(/counts under every tag it has/, { selector: 'p' })).toBeInTheDocument()
  })

  it('shows the firm and kind of each account under its name', async () => {
    const { user } = await openReports()
    await user.click(screen.getByRole('button', { name: 'Account' }))
    await waitFor(() => expect(pressed()).toEqual(['Account']))
    const first = bodyRows()[0] as HTMLElement
    expect(within(first).getByRole('rowheader')).toHaveTextContent(/ · (Funded|Evaluation)$/)
  })
})

describe('the address', () => {
  it('falls back to contract for a breakdown the app does not have, rather than an error', async () => {
    await openReports({ by: 'colour' })
    expect(pressed()).toEqual(['Contract'])
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('names the timezone only where the time of day is read in it', async () => {
    await openReports()
    expect(screen.queryByText(/America\/Los_Angeles/)).not.toBeInTheDocument()
  })

  it('opens on the breakdown it names', async () => {
    await openReports({ by: 'hold' })
    expect(pressed()).toEqual(['Hold time'])
    expect(bodyRows()).toHaveLength(7)
  })

  it('applies the filters in it', async () => {
    await openReports({ symbol_roots: ROOTS[0] as string })
    expect(bodyRows()).toHaveLength(1)
    expect(rowOf(ROOTS[0] as string)).toHaveTextContent(formatPnl(netOf(byRoot(ROOTS[0] as string))))
  })

  it('uses the date range in it, so a week is a week and not the whole journal', async () => {
    await openReports({ preset: '7d' })
    const week = await demoSource.dashboard({ preset: '7d' })
    const everything = await demoSource.dashboard({ preset: 'all' })
    expect(week.summary.trade_count).toBeLessThan(everything.summary.trade_count)
    const total = within(screen.getByRole('table').querySelector('tfoot') as HTMLElement).getByRole('row')
    expect(total).toHaveTextContent(formatPnl(week.summary.net_pnl))
    expect(total).toHaveTextContent(String(week.summary.trade_count))
  })

  it('uses the default date range when none is given, like the dashboard', async () => {
    stubApi()
    const { router } = openApp('/reports')
    await screen.findByRole('table')
    expect(router.state.location.search).not.toHaveProperty('preset') // nothing is added to the address on its own
    const dashboard = await demoSource.dashboard({ preset: '30d' })
    const total = within(screen.getByRole('table').querySelector('tfoot') as HTMLElement).getByRole('row')
    expect(total).toHaveTextContent(formatPnl(dashboard.summary.net_pnl))
  })
})

describe('when there is nothing to show', () => {
  it('says so when the filters match no trades, and "Show everything" brings them back', async () => {
    const { user } = await openReports({ symbol_roots: 'ZZZ' }, { expectTable: false })
    expect(await screen.findByText('No trades match these filters')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show everything' }))
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(bodyRows()).toHaveLength(ROOTS.length)
  })

  it('keeps the chosen breakdown when "Show everything" is used', async () => {
    const { user } = await openReports({ by: 'weekday', symbol_roots: 'ZZZ' }, { expectTable: false })
    await user.click(await screen.findByRole('button', { name: 'Show everything' }))
    await screen.findByRole('table')
    expect(pressed()).toEqual(['Weekday'])
  })

  it('offers to try again when the report cannot be loaded, and loads it then', async () => {
    const failing = vi.spyOn(demoSource, 'report').mockRejectedValueOnce(new Error('network'))
    stubApi()
    const { user } = { user: userEvent.setup({ applyAccept: false }) }
    openApp('/reports', { preset: 'all' })
    expect(await screen.findByText('The report could not be loaded.')).toBeInTheDocument()
    expect(failing).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('table')).toBeInTheDocument()
  })
})
