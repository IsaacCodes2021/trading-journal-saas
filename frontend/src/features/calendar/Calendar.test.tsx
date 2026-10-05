import Decimal from 'decimal.js'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dayLabel } from '../../lib/calendar'
import { SAMPLE_TRADES } from '../../lib/demo/generate'
import { resetDemoJournal } from '../../lib/demo/journal'
import { demoSource } from '../../lib/demo/source'
import { formatPnl, pluralize } from '../../lib/format'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoJournal()
})

const days = [...new Set(SAMPLE_TRADES.map((t) => t.trade_day))].sort()
const FIRST = days[0] as string // 3 Sep 2026
const MONTH = FIRST.slice(0, 7)
const LATEST_MONTH = (days.at(-1) as string).slice(0, 7)

const tradesOn = (day: string) => SAMPLE_TRADES.filter((t) => t.trade_day === day)
const netOf = (trades: readonly { net_pnl: string }[]) =>
  trades.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2)

async function openCalendar(search: Record<string, string> = { month: MONTH }) {
  stubApi()
  const app = openApp('/calendar', search)
  await screen.findByRole('region', { name: 'Calendar' }).catch(() => null)
  return { ...app, user: userEvent.setup({ applyAccept: false }) }
}

const dayLink = (day: string) => screen.getByRole('link', { name: new RegExp(`^${dayLabel(day)}`) })
const location = (app: Awaited<ReturnType<typeof openCalendar>>) => app.router.state.location

describe('the month', () => {
  it('names the month, the weekdays from Monday, and a column for each week', async () => {
    await openCalendar()
    expect(await screen.findByRole('heading', { name: 'September 2026' })).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((h) => h.textContent),
    ).toEqual(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun', 'Week'])
  })

  it('has a link for every day of the month, and only for them', async () => {
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    const region = screen.getByRole('region', { name: 'Calendar' })
    expect(within(region).getAllByRole('link')).toHaveLength(30) // September
    expect(dayLink('2026-09-01')).toBeInTheDocument()
    expect(dayLink('2026-09-30')).toBeInTheDocument()
  })

  it('puts the result and the count of each day that traded in the name of its link, and "no trades" in the others', async () => {
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    const traded = tradesOn(FIRST)
    expect(dayLink(FIRST)).toHaveAccessibleName(
      `${dayLabel(FIRST)}, ${formatPnl(netOf(traded))} from ${pluralize(traded.length, 'trade')}`,
    )
    expect(dayLink('2026-09-05')).toHaveAccessibleName(`${dayLabel('2026-09-05')}, no trades`) // a Saturday
  })

  it('shows the same result and count inside the cell', async () => {
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    const traded = tradesOn(FIRST)
    const cell = dayLink(FIRST)
    expect(cell).toHaveTextContent(formatPnl(netOf(traded)))
    expect(cell).toHaveTextContent(pluralize(traded.length, 'trade'))
    expect(cell).toHaveTextContent(String(Number(FIRST.slice(8))))
  })

  it('totals each week next to it, exactly', async () => {
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1) // after the header
    const firstWeek = tradesOn(FIRST).concat(
      days.filter((d) => d > FIRST && d <= '2026-09-06').flatMap((d) => tradesOn(d)),
    ) // Tue 1 to Sun 6 September
    const weekCell = within(rows[0] as HTMLElement)
      .getAllByRole('cell')
      .at(-1) as HTMLElement
    expect(weekCell).toHaveTextContent(formatPnl(netOf(firstWeek)))
    expect(weekCell).toHaveTextContent(pluralize(firstWeek.length, 'trade'))
    expect(rows.length).toBeGreaterThanOrEqual(5)
  })

  it('shades a day by how big it is against the biggest day of the month', async () => {
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    const inMonth = days.filter((d) => d.startsWith(MONTH))
    const nets = inMonth.map((d) => ({ day: d, net: new Decimal(netOf(tradesOn(d))) }))
    const biggest = nets.reduce((top, d) => (d.net.abs().gt(top.net.abs()) ? d : top))
    expect(dayLink(biggest.day).closest('td')).toHaveClass(biggest.net.isNegative() ? 'bg-neg/35' : 'bg-pos/35')
    const flat = dayLink('2026-09-05').closest('td') as HTMLElement
    expect(flat.className).not.toMatch(/bg-(pos|neg)/)
  })
})

describe('the month at a glance', () => {
  it('shows the month’s net result, its trades and its trading days', async () => {
    await openCalendar()
    const tiles = await screen.findByRole('region', { name: 'The month at a glance' }).catch(() => null)
    const glance = tiles ?? (await screen.findByLabelText('The month at a glance'))
    const inMonth = SAMPLE_TRADES.filter((t) => t.trade_day.startsWith(`${MONTH}-`))
    const tradingDays = new Set(inMonth.map((t) => t.trade_day)).size
    expect(glance).toHaveTextContent(formatPnl(netOf(inMonth)))
    expect(glance).toHaveTextContent(`Trades${inMonth.length}`)
    expect(glance).toHaveTextContent(`Trading days${tradingDays}`)
  })

  it('names the best and the worst day', async () => {
    await openCalendar()
    const glance = await screen.findByLabelText('The month at a glance')
    const nets = days
      .filter((d) => d.startsWith(MONTH))
      .map((d) => new Decimal(netOf(tradesOn(d))))
      .sort((a, b) => a.comparedTo(b))
    expect(within(glance).getByText('Best day').parentElement).toHaveTextContent(
      formatPnl((nets.at(-1) as Decimal).toFixed(2)),
    )
    expect(within(glance).getByText('Worst day').parentElement).toHaveTextContent(
      formatPnl((nets[0] as Decimal).toFixed(2)),
    )
  })
})

describe('moving between months', () => {
  it('shows the latest month with trades when the address names none, and offers no jump to it', async () => {
    await openCalendar({})
    expect(await screen.findByRole('heading', { name: 'October 2026' })).toBeInTheDocument()
    expect(LATEST_MONTH).toBe('2026-10')
    expect(screen.queryByRole('button', { name: 'Latest month with trades' })).not.toBeInTheDocument()
  })

  it('offers the way back to the latest month from an earlier one', async () => {
    const app = await openCalendar()
    await userEvent.click(await screen.findByRole('button', { name: 'Latest month with trades' }))
    expect(await screen.findByRole('heading', { name: 'October 2026' })).toBeInTheDocument()
    expect(location(app).search).toMatchObject({ month: '2026-10' })
  })

  it('pages by month, writes the month to the address and keeps the filters', async () => {
    const app = await openCalendar({ month: '2026-09', direction: 'long' })
    await userEvent.click(await screen.findByRole('button', { name: 'Next month' }))
    expect(await screen.findByRole('heading', { name: 'October 2026' })).toBeInTheDocument()
    expect(location(app).search).toMatchObject({ month: '2026-10', direction: 'long' })
    await userEvent.click(screen.getByRole('button', { name: 'Previous month' }))
    await userEvent.click(screen.getByRole('button', { name: 'Previous month' }))
    expect(await screen.findByRole('heading', { name: 'August 2026' })).toBeInTheDocument()
    expect(location(app).search).toMatchObject({ month: '2026-08', direction: 'long' })
  })

  it('cannot go before the first month or after the last the app has', async () => {
    await openCalendar({ month: '2000-01' })
    expect(await screen.findByRole('button', { name: 'Previous month' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next month' })).toBeEnabled()
  })

  it('cannot go after the last month', async () => {
    await openCalendar({ month: '2100-12' })
    expect(await screen.findByRole('button', { name: 'Next month' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous month' })).toBeEnabled()
  })

  it('drops a month that is not one and shows the latest instead', async () => {
    for (const bad of ['2026-13', 'soon', '1999-05', '2026-9']) {
      const { unmount } = await openCalendar({ month: bad })
      expect(await screen.findByRole('heading', { name: 'October 2026' })).toBeInTheDocument()
      unmount()
    }
  })
})

describe('a month with nothing in it', () => {
  it('says so, still shows every day, and lets any of them be opened', async () => {
    await openCalendar({ month: '2026-08' })
    expect(await screen.findByRole('status')).toHaveTextContent('No trades in August 2026.')
    expect(within(screen.getByRole('region', { name: 'Calendar' })).getAllByRole('link')).toHaveLength(31)
    expect(dayLink('2026-08-12')).toHaveAccessibleName(`${dayLabel('2026-08-12')}, no trades`)
  })

  it('says which filters it was with', async () => {
    await openCalendar({ month: '2026-08', direction: 'long' })
    expect(await screen.findByRole('status')).toHaveTextContent('No trades in August 2026 with these filters.')
  })

  it('has no best or worst day, and a dash for every week', async () => {
    await openCalendar({ month: '2026-08' })
    const glance = await screen.findByLabelText('The month at a glance')
    expect(within(glance).getByText('Best day').parentElement).toHaveTextContent('Best day—')
    const weeks = within(screen.getByRole('table')).getAllByRole('row').slice(1)
    for (const week of weeks) expect(within(week).getAllByRole('cell').at(-1)).toHaveTextContent('—')
  })
})

describe('days with a note', () => {
  it('are marked in the name of their link', async () => {
    await demoSource.saveDayNote(FIRST, '<p>Slow open</p>')
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    expect(dayLink(FIRST)).toHaveAccessibleName(expect.stringMatching(/, has a note$/))
    expect(dayLink('2026-09-04')).not.toHaveAccessibleName(expect.stringMatching(/has a note/))
  })

  it('show a dot in the corner of the cell, and no other day does', async () => {
    await demoSource.saveDayNote(FIRST, '<p>Slow open</p>')
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    expect(dayLink(FIRST).querySelector('.bg-accent')).not.toBeNull()
    expect(dayLink('2026-09-04').querySelector('.bg-accent')).toBeNull()
  })

  it('are marked on a day with no trades too', async () => {
    await demoSource.saveDayNote('2026-09-05', '<p>Out of the market</p>')
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    expect(dayLink('2026-09-05')).toHaveAccessibleName(`${dayLabel('2026-09-05')}, no trades, has a note`)
  })
})

describe('opening a day', () => {
  it('goes to the day page, carrying the filters', async () => {
    const app = await openCalendar({ month: MONTH, direction: 'long' })
    await screen.findByRole('region', { name: 'Calendar' })
    await userEvent.click(dayLink(FIRST))
    await waitFor(() => expect(location(app).pathname).toBe(`/day/${FIRST}`))
    expect(location(app).search).toMatchObject({ direction: 'long' })
  })
})

describe('the filters', () => {
  it('leave out the date range, because the month is the range, and keep the rest', async () => {
    await openCalendar()
    await screen.findByRole('region', { name: 'Calendar' })
    expect(screen.queryByRole('group', { name: 'Date range' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /All account types/ })).toBeInTheDocument()
    expect(screen.getByRole('search', { name: 'Filters' })).toHaveTextContent('The month is the date range')
  })

  it('keep the month when one is changed', async () => {
    const app = await openCalendar({ month: MONTH })
    await userEvent.click(await screen.findByRole('button', { name: /Long \+ short/ }))
    await userEvent.click(screen.getByRole('radio', { name: 'Long only' }))
    await waitFor(() => expect(location(app).search).toMatchObject({ month: MONTH, direction: 'long' }))
  })

  it('narrow the month', async () => {
    await openCalendar({ month: MONTH, direction: 'long' })
    const glance = await screen.findByLabelText('The month at a glance')
    const longs = SAMPLE_TRADES.filter((t) => t.trade_day.startsWith(`${MONTH}-`) && t.direction === 'long')
    expect(glance).toHaveTextContent(`Trades${longs.length}`)
  })
})

describe('when something goes wrong', () => {
  it('says the calendar could not be loaded and tries again on request', async () => {
    vi.spyOn(demoSource, 'calendar').mockRejectedValueOnce(new Error('offline'))
    await openCalendar()
    expect(await screen.findByRole('alert')).toHaveTextContent('The calendar could not be loaded.')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('region', { name: 'Calendar' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
