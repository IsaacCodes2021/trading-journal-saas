import Decimal from 'decimal.js'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dayLabel } from '../../lib/calendar'
import { SAMPLE_TRADES } from '../../lib/demo/generate'
import { resetDemoJournal } from '../../lib/demo/journal'
import { demoSource } from '../../lib/demo/source'
import { formatClock, formatHourMinute, formatPnl, pluralize } from '../../lib/format'
import { openApp, stubApi } from '../../test-utils'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  resetDemoJournal()
})

const days = [...new Set(SAMPLE_TRADES.map((t) => t.trade_day))].sort()
const FIRST = days[0] as string
const TZ = 'America/Los_Angeles' // the stubbed account's timezone

const tradesOn = (day: string) =>
  SAMPLE_TRADES.filter((t) => t.trade_day === day).sort((a, b) => (a.entry_at < b.entry_at ? -1 : 1))
const netOf = (trades: readonly { net_pnl: string }[]) =>
  trades.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2)

async function openDay(day: string, search: Record<string, string> = {}) {
  stubApi()
  const app = openApp(`/day/${day}`, search)
  await screen.findByRole('heading', { name: 'Day', hidden: true }).catch(() => null)
  return { ...app, user: userEvent.setup({ applyAccept: false }) }
}

describe('a day with trades', () => {
  it('names the day in full', async () => {
    await openDay(FIRST)
    expect(await screen.findByRole('heading', { name: dayLabel(FIRST) })).toBeInTheDocument()
  })

  it('shows its result, its trades and its fees', async () => {
    await openDay(FIRST)
    const glance = await screen.findByLabelText('The day at a glance')
    const trades = tradesOn(FIRST)
    expect(glance).toHaveTextContent(formatPnl(netOf(trades)))
    expect(glance).toHaveTextContent(`Trades${trades.length}`)
  })

  it('lists every trade, oldest first, each a link to its own page, with the time in the account’s zone', async () => {
    await openDay(FIRST)
    const region = await screen.findByRole('region', { name: 'Trades of the day' })
    const trades = tradesOn(FIRST)
    const rows = within(region).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(trades.length)
    trades.forEach((trade, index) => {
      const link = within(rows[index] as HTMLElement).getByRole('link')
      expect(link).toHaveTextContent(formatClock(trade.entry_at, TZ))
      expect(link).toHaveAttribute('href', expect.stringContaining(`/trades/${trade.id}`))
      expect(rows[index]).toHaveTextContent(trade.symbol)
      expect(rows[index]).toHaveTextContent(formatPnl(trade.net_pnl))
    })
    expect(within(region).getByText(pluralize(trades.length, 'trade'))).toBeInTheDocument()
  })

  it('draws the day’s equity when there is more than one trade', async () => {
    await openDay(FIRST)
    expect(tradesOn(FIRST).length).toBeGreaterThan(1)
    expect(await screen.findByRole('region', { name: "The day's equity" })).toBeInTheDocument()
  })

  it('labels the equity line with the time of day, not the date, since every point is on the same day', async () => {
    await openDay(FIRST)
    const chart = await screen.findByRole('img', { name: /^Equity curve/ })
    const points = (await demoSource.day(FIRST, { preset: 'all' })).equity_by_trade
    expect(chart).toHaveTextContent(formatHourMinute(points[0]?.at as string, TZ))
    expect(chart).toHaveTextContent(formatHourMinute(points.at(-1)?.at as string, TZ))
    expect(chart).not.toHaveTextContent('Sep')
  })

  it('shows the largest win and the largest loss as tiles of their own', async () => {
    await openDay(FIRST)
    const glance = await screen.findByLabelText('The day at a glance')
    const nets = tradesOn(FIRST).map((t) => new Decimal(t.net_pnl))
    const wins = nets.filter((n) => n.gt(0))
    const losses = nets.filter((n) => n.lt(0))
    expect(within(glance).getByText('Largest win').parentElement).toHaveTextContent(
      formatPnl(Decimal.max(...wins).toFixed(2)),
    )
    expect(within(glance).getByText('Largest loss').parentElement).toHaveTextContent(
      formatPnl(Decimal.min(...losses).toFixed(2)),
    )
  })

  it('draws no equity for a day with a single trade', async () => {
    const one = { ...(await demoSource.day(FIRST, { preset: 'all' })) }
    vi.spyOn(demoSource, 'day').mockResolvedValue({ ...one, trades: one.trades.slice(0, 1), total: 1 })
    await openDay(FIRST)
    await screen.findByRole('region', { name: 'Trades of the day' })
    expect(screen.queryByRole('region', { name: "The day's equity" })).not.toBeInTheDocument()
  })

  it('says when a very busy day was cut off', async () => {
    const real = await demoSource.day(FIRST, { preset: 'all' })
    vi.spyOn(demoSource, 'day').mockResolvedValue({ ...real, total: 250 })
    await openDay(FIRST)
    const region = await screen.findByRole('region', { name: 'Trades of the day' })
    expect(region).toHaveTextContent(`The first ${pluralize(real.trades.length, 'trade')} of 250`)
  })

  it('keeps the filters: only the trades they let through, and in the links to the other days', async () => {
    await openDay(FIRST, { direction: 'long' })
    const region = await screen.findByRole('region', { name: 'Trades of the day' })
    const longs = tradesOn(FIRST).filter((t) => t.direction === 'long')
    expect(within(region).getAllByRole('row').slice(1)).toHaveLength(longs.length)
    expect(screen.getByRole('link', { name: 'Next day' }).getAttribute('href')).toContain('direction=long')
  })
})

describe('a day with no trades', () => {
  const SATURDAY = '2026-09-05'

  it('says so and still offers the note', async () => {
    await openDay(SATURDAY)
    expect(await screen.findByText(/^No trades on this day\./)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Trades of the day' })).not.toBeInTheDocument()
    expect(await screen.findByRole('textbox', { name: 'Day note' })).toBeInTheDocument()
  })

  it('says which filters it was with', async () => {
    await openDay(SATURDAY, { direction: 'short' })
    expect(await screen.findByText(/^No trades on this day with these filters\./)).toBeInTheDocument()
  })
})

describe('the note for the day', () => {
  it('starts empty and saves by itself a moment after typing stops', async () => {
    const { user } = await openDay(FIRST)
    const box = await screen.findByRole('textbox', { name: 'Day note' })
    expect(box).toHaveTextContent('')
    await user.click(box)
    await user.keyboard('Slow open, then a trend.')
    expect(await screen.findByText('Unsaved changes')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument(), { timeout: 3000 })
    expect(await demoSource.dayNote(FIRST)).toBe('<p>Slow open, then a trend.</p>')
  })

  it('saves at once when the editor loses focus', async () => {
    const { user } = await openDay(FIRST)
    const box = await screen.findByRole('textbox', { name: 'Day note' })
    await user.click(box)
    await user.keyboard('Quick note')
    await user.click(screen.getByRole('heading', { name: dayLabel(FIRST) }))
    await waitFor(async () => expect(await demoSource.dayNote(FIRST)).toBe('<p>Quick note</p>'))
  })

  it('shows what was written before, and is its own for each day', async () => {
    await demoSource.saveDayNote(FIRST, '<p>Written earlier</p>')
    await openDay(FIRST)
    expect(await screen.findByRole('textbox', { name: 'Day note' })).toHaveTextContent('Written earlier')
  })

  it('is named differently from a trade’s notes, so a screen reader can tell them apart', async () => {
    await openDay(FIRST)
    const card = await screen.findByRole('region', { name: 'Day note' })
    expect(within(card).getByRole('heading', { name: 'Day note' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Notes' })).not.toBeInTheDocument()
  })

  it('keeps what was typed and offers another try when saving fails', async () => {
    const save = vi.spyOn(demoSource, 'saveDayNote').mockRejectedValueOnce(new Error('offline'))
    const { user } = await openDay(FIRST)
    const box = await screen.findByRole('textbox', { name: 'Day note' })
    await user.click(box)
    await user.keyboard('Do not lose me')
    await user.click(screen.getByRole('heading', { name: dayLabel(FIRST) }))
    expect(await screen.findByText('Could not save. Your text is still here.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument())
    expect(save).toHaveBeenCalledTimes(2)
    expect(await demoSource.dayNote(FIRST)).toBe('<p>Do not lose me</p>')
  })

  it('says when the note could not be loaded, and tries again on request', async () => {
    vi.spyOn(demoSource, 'dayNote').mockRejectedValueOnce(new Error('offline'))
    const { user } = await openDay(FIRST)
    expect(await screen.findByText('The note for this day could not be loaded.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('textbox', { name: 'Day note' })).toBeInTheDocument()
  })

  it('marks the day on the calendar once it has been saved', async () => {
    const { user } = await openDay(FIRST)
    const box = await screen.findByRole('textbox', { name: 'Day note' })
    await user.click(box)
    await user.keyboard('Remember this')
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument(), { timeout: 3000 })
    await user.click(screen.getByRole('link', { name: '‹ Calendar' }))
    const link = await screen.findByRole('link', { name: new RegExp(`^${dayLabel(FIRST)}.*has a note$`) })
    expect(link).toBeInTheDocument()
  })
})

describe('moving around', () => {
  it('goes to the day before and the day after, keeping the filters', async () => {
    const app = await openDay('2026-09-08', { direction: 'long' })
    expect(await screen.findByRole('link', { name: 'Previous day' })).toHaveAttribute(
      'href',
      expect.stringContaining('/day/2026-09-07'),
    )
    await userEvent.click(screen.getByRole('link', { name: 'Next day' }))
    expect(await screen.findByRole('heading', { name: dayLabel('2026-09-09') })).toBeInTheDocument()
    expect(app.router.state.location.pathname).toBe('/day/2026-09-09')
    expect(app.router.state.location.search).toMatchObject({ direction: 'long' })
  })

  it('crosses a month', async () => {
    await openDay('2026-09-30')
    await userEvent.click(await screen.findByRole('link', { name: 'Next day' }))
    expect(await screen.findByRole('heading', { name: dayLabel('2026-10-01') })).toBeInTheDocument()
  })

  it('goes back to the calendar at this day’s month, keeping the filters', async () => {
    const app = await openDay('2026-09-08', { direction: 'long' })
    const back = await screen.findByRole('link', { name: '‹ Calendar' })
    expect(back.getAttribute('href')).toContain('month=2026-09')
    await userEvent.click(back)
    await waitFor(() => expect(app.router.state.location.pathname).toBe('/calendar'))
    expect(app.router.state.location.search).toMatchObject({ month: '2026-09', direction: 'long' })
  })

  it('cannot go before the first day or after the last the app has', async () => {
    await openDay('2000-01-01')
    expect(await screen.findByRole('button', { name: 'Previous day' })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Next day' })).toBeInTheDocument()
  })

  it('can go back from the second day and forward from the day before the last', async () => {
    const second = await openDay('2000-01-02')
    expect(await screen.findByRole('link', { name: 'Previous day' })).toHaveAttribute(
      'href',
      expect.stringContaining('/day/2000-01-01'),
    )
    second.unmount() // so the other page's links are not what is found below
    await openDay('2100-12-30')
    expect(await screen.findByRole('link', { name: 'Next day' })).toHaveAttribute(
      'href',
      expect.stringContaining('/day/2100-12-31'),
    )
  })

  it('does not show the day it came from while the next one loads', async () => {
    const real = demoSource.day.bind(demoSource)
    const { user } = await openDay('2026-09-08')
    await screen.findByRole('region', { name: 'Trades of the day' })
    vi.spyOn(demoSource, 'day').mockImplementation((day, filters, signal) =>
      day === '2026-09-09' ? new Promise(() => undefined) : real(day, filters, signal),
    )
    await user.click(screen.getByRole('link', { name: 'Next day' }))
    expect(await screen.findByRole('heading', { name: dayLabel('2026-09-09') })).toBeInTheDocument()
    expect(screen.getByText('Loading the day…')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Trades of the day' })).not.toBeInTheDocument() // not the 8th's trades
  })

  it('cannot go after the last day', async () => {
    await openDay('2100-12-31')
    expect(await screen.findByRole('button', { name: 'Next day' })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Previous day' })).toBeInTheDocument()
  })

  it('stays on the day when a filter is changed', async () => {
    const app = await openDay(FIRST)
    await userEvent.click(await screen.findByRole('button', { name: /Long \+ short/ }))
    await userEvent.click(screen.getByRole('radio', { name: 'Long only' }))
    await waitFor(() => expect(app.router.state.location.search).toMatchObject({ direction: 'long' }))
    expect(app.router.state.location.pathname).toBe(`/day/${FIRST}`)
  })

  it('leaves out the date range from the filters, because the day is the range', async () => {
    await openDay(FIRST)
    await screen.findByRole('region', { name: 'Trades of the day' })
    expect(screen.queryByRole('group', { name: 'Date range' })).not.toBeInTheDocument()
    expect(screen.getByRole('search', { name: 'Filters' })).toHaveTextContent('The day is the date range')
  })
})

describe('an address that is not a day', () => {
  it.each(['2026-02-30', '2026-13-01', 'yesterday', '1999-12-31', '2101-01-01', '2026-9-8'])(
    'says so, for %s, and offers the calendar',
    async (bad) => {
      await openDay(bad, { direction: 'long' })
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('That is not a day this app has')
      const back = within(alert).getByRole('link', { name: 'Back to the calendar' })
      expect(back.getAttribute('href')).toContain('direction=long')
      expect(screen.queryByRole('textbox', { name: 'Day note' })).not.toBeInTheDocument()
    },
  )
})

describe('when the day cannot be loaded', () => {
  it('says so and tries again on request', async () => {
    vi.spyOn(demoSource, 'day').mockRejectedValueOnce(new Error('offline'))
    const { user } = await openDay(FIRST)
    expect(await screen.findByText('This day could not be loaded.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('region', { name: 'Trades of the day' })).toBeInTheDocument()
  })
})
