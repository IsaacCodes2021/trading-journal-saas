import Decimal from 'decimal.js'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildWeeks, shiftDay, weekLabel, weekStart } from '../../lib/calendar'
import { SAMPLE_TRADES } from '../../lib/demo/generate'
import { resetDemoJournal, saveDayNote } from '../../lib/demo/journal'
import { demoSource } from '../../lib/demo/source'
import { formatLongDay, formatPnl } from '../../lib/format'
import { openApp, stubApi, type ApiOptions } from '../../test-utils'
import { cardToBlob, saveBlob } from './exportImage'

// jsdom has no canvas: the image itself was checked in a browser. What is tested is that Download hands the still card
// to the export, under the right name.
vi.mock('./exportImage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./exportImage')>()),
  cardToBlob: vi.fn(async () => new Blob(['png'], { type: 'image/png' })),
  saveBlob: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  resetDemoJournal()
  window.localStorage.clear()
})

const days = [...new Set(SAMPLE_TRADES.map((t) => t.trade_day))].sort()
const FIRST = days[0] as string // the sample journal's first trading day
const MONTH = FIRST.slice(0, 7)
const ALL = { preset: 'all' } as const

const netOf = (from: string, to: string) =>
  SAMPLE_TRADES.filter((t) => t.trade_day >= from && t.trade_day <= to)
    .reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0))
    .toFixed(2)

/** A week of the sample journal that traded on both sides of a month's end. */
const crossing = days.map(weekStart).find((sunday) => {
  const traded = days.filter((d) => weekStart(d) === sunday)
  return new Set(traded.map((d) => d.slice(0, 7))).size > 1
}) as string

async function openCalendar(month: string, api: ApiOptions = {}) {
  stubApi(api)
  openApp('/calendar', { month })
  await screen.findByRole('region', { name: 'Calendar' })
  return userEvent.setup({ applyAccept: false })
}

async function openDay(day: string, search: Record<string, string> = {}, api: ApiOptions = {}) {
  stubApi(api)
  openApp(`/day/${day}`, search)
  await screen.findByRole('region', { name: 'Trades of the day' }).catch(() => null)
  return userEvent.setup({ applyAccept: false })
}

const weekButton = (sunday: string) =>
  screen.getByRole('button', { name: new RegExp(`^Share the week of ${weekLabel(sunday)}: `) })

/** The card in the pop-up, once its numbers have loaded. */
const cardIn = (dialog: HTMLElement) => within(dialog).findByRole('group', { name: /share card,/ })

describe('sharing a week from the calendar', () => {
  it('offers Share on every week that traded, and on no other', async () => {
    await openCalendar(MONTH)
    const month = await demoSource.calendar(MONTH, ALL)
    const traded = buildWeeks(MONTH, month.daily, []).filter((w) => w.net !== null)
    const buttons = screen.getAllByRole('button', { name: /^Share the week of / })
    expect(buttons).toHaveLength(traded.length)
    for (const week of traded) expect(weekButton(week.start)).toHaveAttribute('aria-haspopup', 'dialog')
  })

  it('opens "Share week" for the whole week, Sunday to Saturday, across the month’s end', async () => {
    const user = await openCalendar(crossing.slice(0, 7))
    await user.click(weekButton(crossing))
    const dialog = await screen.findByRole('dialog', { name: 'Share week' })
    const card = await cardIn(dialog)
    expect(card).toHaveAccessibleName('Week share card, gold foil style')
    expect(card).toHaveTextContent(weekLabel(crossing))
    expect(card).toHaveTextContent('Certificate of the Week')
    expect(card).toHaveTextContent(formatPnl(netOf(crossing, shiftDay(crossing, 6))))
    expect(card).toHaveTextContent('Best trade')
    expect(card).toHaveTextContent('Worst trade')
  })

  it('saves the image under the week’s Sunday and its number', async () => {
    const user = await openCalendar(MONTH)
    const sunday = buildWeeks(MONTH, (await demoSource.calendar(MONTH, ALL)).daily, []).find((w) => w.net)?.start
    await user.click(weekButton(sunday as string))
    const dialog = await screen.findByRole('dialog', { name: 'Share week' })
    await user.click(within(dialog).getByRole('button', { name: 'Download image' }))
    await waitFor(() => expect(saveBlob).toHaveBeenCalledTimes(1))
    expect(cardToBlob).toHaveBeenCalledWith(document.querySelector('.sc-offstage .sc-card'))
    const name = vi.mocked(saveBlob).mock.calls[0]?.[1]
    expect(name).toMatch(new RegExp(`^roundturn-week-${sunday}-no-\\d{5}\\.png$`))
    expect(await within(dialog).findByText('Image saved.')).toBeInTheDocument()
  })

  it('does not open over the "Pick a username" box, and opens once the box is put away', async () => {
    const user = await openCalendar(MONTH, { askUsername: true })
    expect(await screen.findByRole('dialog', { name: 'Pick a username' })).toBeInTheDocument()
    const sunday = buildWeeks(MONTH, (await demoSource.calendar(MONTH, ALL)).daily, []).find((w) => w.net)?.start
    await user.click(weekButton(sunday as string))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.queryByRole('dialog', { name: 'Share week' })).toBeNull()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Pick a username' })).toBeNull()
    await user.click(weekButton(sunday as string))
    expect(await screen.findByRole('dialog', { name: 'Share week' })).toBeInTheDocument()
  })
})

describe('sharing a day', () => {
  it('puts Share day in the day’s bar, opening "Share day" with the day’s numbers', async () => {
    const user = await openDay(FIRST)
    await user.click(await screen.findByRole('button', { name: 'Share day' }))
    const dialog = await screen.findByRole('dialog', { name: 'Share day' })
    const card = await cardIn(dialog)
    expect(card).toHaveAccessibleName('Day share card, gold foil style')
    expect(card).toHaveTextContent(formatLongDay(FIRST))
    expect(card).toHaveTextContent('Certificate of the Day')
    expect(card).toHaveTextContent(formatPnl(netOf(FIRST, FIRST)))
    for (const label of ['Trades', 'Contracts', 'Net points', 'Win rate']) expect(card).toHaveTextContent(label)
  })

  it('is not offered on a day with no trades', async () => {
    await openDay('2026-08-01')
    await screen.findByText(/No trades on this day/)
    expect(screen.queryByRole('button', { name: 'Share day' })).toBeNull()
  })

  it('puts the day’s note on the card unless it is switched off, and says when there is none', async () => {
    saveDayNote(FIRST, '<p>Calm open, two clean trades.</p>')
    const user = await openDay(FIRST)
    await user.click(await screen.findByRole('button', { name: 'Share day' }))
    const dialog = await screen.findByRole('dialog', { name: 'Share day' })
    const option = await within(dialog).findByRole('checkbox', { name: /Day note/ })
    await waitFor(() => expect(option).toBeChecked())
    expect(await cardIn(dialog)).toHaveTextContent('Calm open, two clean trades.')
    await user.click(option)
    expect(await cardIn(dialog)).not.toHaveTextContent('Calm open')
  })

  it('has the note option switched off when the day has no note', async () => {
    const user = await openDay(FIRST)
    await user.click(await screen.findByRole('button', { name: 'Share day' }))
    const dialog = await screen.findByRole('dialog', { name: 'Share day' })
    const option = await within(dialog).findByRole('checkbox', { name: /Day note/ })
    expect(option).toBeDisabled()
    expect(within(dialog).getByText('This day has no note.')).toBeInTheDocument()
  })

  it('switches to points: the net points become the result and no dollar is left', async () => {
    const user = await openDay(FIRST)
    await user.click(await screen.findByRole('button', { name: 'Share day' }))
    const dialog = await screen.findByRole('dialog', { name: 'Share day' })
    await user.click(within(dialog).getByRole('button', { name: 'Points only' }))
    const card = await cardIn(dialog)
    expect(card).toHaveTextContent('Net points')
    expect(card.textContent).not.toContain('$')
    expect(card).toHaveTextContent('Contracts')
  })

  it('follows the page’s filters, and says so', async () => {
    const user = await openDay(FIRST, { direction: 'short' })
    await user.click(await screen.findByRole('button', { name: 'Share day' }))
    const dialog = await screen.findByRole('dialog', { name: 'Share day' })
    const shorts = SAMPLE_TRADES.filter((t) => t.trade_day === FIRST && t.direction === 'short')
    const net = shorts.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2)
    expect(await cardIn(dialog)).toHaveTextContent(formatPnl(net))
    expect(within(dialog).getByText('Only the trades your filters keep are on the card.')).toBeInTheDocument()
  })
})
