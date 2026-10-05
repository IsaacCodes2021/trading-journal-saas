import Decimal from 'decimal.js'
import { afterEach, describe, expect, it } from 'vitest'
import { SAMPLE_TRADES } from './generate'
import { resetDemoJournal } from './journal'
import { demoSource } from './source'

afterEach(resetDemoJournal)

const days = [...new Set(SAMPLE_TRADES.map((t) => t.trade_day))].sort()
const FIRST = days[0] as string
const MONTH = FIRST.slice(0, 7)
const NO_FILTERS = { preset: '30d' } as const // the date range must make no difference: the month is the range

describe('the calendar of the sample journal', () => {
  it('has the days that traded in the month, with their exact results and counts', async () => {
    const month = await demoSource.calendar(MONTH, NO_FILTERS)
    const inMonth = SAMPLE_TRADES.filter((t) => t.trade_day.startsWith(`${MONTH}-`))
    expect(month.daily.map((d) => d.day)).toEqual([...new Set(inMonth.map((t) => t.trade_day))].sort())
    for (const d of month.daily) {
      const that = inMonth.filter((t) => t.trade_day === d.day)
      expect(d.trade_count).toBe(that.length)
      expect(d.net_pnl).toBe(that.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2))
    }
    expect(month.summary.trade_count).toBe(inMonth.length)
    expect(month.summary.net_pnl).toBe(inMonth.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2))
  })

  it('is empty for a month with no trades, and leaves out the days of other months', async () => {
    const empty = await demoSource.calendar('2020-01', NO_FILTERS)
    expect(empty.daily).toEqual([])
    expect(empty.summary.trade_count).toBe(0)
    const month = await demoSource.calendar(MONTH, NO_FILTERS)
    expect(month.daily.every((d) => d.day.startsWith(`${MONTH}-`))).toBe(true)
  })

  it('ignores the date range of the filters but applies the others', async () => {
    const all = await demoSource.calendar(MONTH, { preset: 'all' })
    const seven = await demoSource.calendar(MONTH, { preset: '7d' })
    expect(seven).toEqual(all)
    const long = await demoSource.calendar(MONTH, { preset: 'all', direction: 'long' })
    expect(long.summary.trade_count).toBe(
      SAMPLE_TRADES.filter((t) => t.trade_day.startsWith(`${MONTH}-`) && t.direction === 'long').length,
    )
    expect(long.summary.trade_count).toBeLessThan(all.summary.trade_count)
  })

  it('marks the days that have a note, only those of the month asked for', async () => {
    expect((await demoSource.calendar(MONTH, NO_FILTERS)).noted).toEqual([])
    await demoSource.saveDayNote(FIRST, '<p>Slow open</p>')
    await demoSource.saveDayNote('2020-01-15', '<p>Another month</p>')
    expect((await demoSource.calendar(MONTH, NO_FILTERS)).noted).toEqual([FIRST])
    expect((await demoSource.calendar('2020-01', NO_FILTERS)).noted).toEqual(['2020-01-15'])
  })

  it('does not take a longer month for a shorter name: "2026-1" is nobody’s month', async () => {
    const odd = await demoSource.calendar('2026-1', NO_FILTERS)
    expect(odd.daily).toEqual([]) // October's days start with "2026-1" but are not in it
    expect(odd.summary.trade_count).toBe(0)
  })

  it('does not take the 1st of the next month for part of this one', async () => {
    await demoSource.saveDayNote('2026-10-01', '<p>x</p>')
    expect((await demoSource.calendar('2026-1', NO_FILTERS)).noted).toEqual([]) // "2026-1" is not "2026-10"
  })
})

describe('one day of the sample journal', () => {
  it('has the trades of that day in the order they were entered, and their statistics', async () => {
    const data = await demoSource.day(FIRST, NO_FILTERS)
    const that = SAMPLE_TRADES.filter((t) => t.trade_day === FIRST).sort((a, b) => (a.entry_at < b.entry_at ? -1 : 1))
    expect(data.trades.map((t) => t.id)).toEqual(that.map((t) => t.id))
    expect(data.total).toBe(that.length)
    expect(data.summary.trade_count).toBe(that.length)
    expect(data.equity_by_trade).toHaveLength(that.length)
    expect(data.equity_by_trade.at(-1)?.equity).toBe(data.summary.net_pnl)
  })

  it('is empty for a day with no trades, and honours the filters', async () => {
    const none = await demoSource.day('2020-01-15', NO_FILTERS)
    expect(none).toMatchObject({ trades: [], total: 0, equity_by_trade: [] })
    const long = await demoSource.day(FIRST, { preset: 'all', direction: 'long' })
    expect(long.trades.every((t) => t.direction === 'long')).toBe(true)
  })

  it('shows the journal as changed: a note on a trade shows in its row', async () => {
    const trade = SAMPLE_TRADES.find((t) => t.trade_day === FIRST && !t.has_notes)
    if (!trade) return
    await demoSource.saveNotes(trade.id, '<p>Held</p>')
    const row = (await demoSource.day(FIRST, NO_FILTERS)).trades.find((t) => t.id === trade.id)
    expect(row?.has_notes).toBe(true)
  })
})

describe('the note for a day in the sample journal', () => {
  it('is empty until written, then read back as written', async () => {
    expect(await demoSource.dayNote(FIRST)).toBe('')
    await demoSource.saveDayNote(FIRST, '  <p>Choppy</p> ')
    expect(await demoSource.dayNote(FIRST)).toBe('<p>Choppy</p>')
  })

  it('is removed when its text is emptied, like the server, so the day stops being marked', async () => {
    await demoSource.saveDayNote(FIRST, '<p>Choppy</p>')
    for (const empty of ['', '   ', '<p></p>', '<p><br></p>']) {
      await demoSource.saveDayNote(FIRST, '<p>Choppy</p>')
      await demoSource.saveDayNote(FIRST, empty)
      expect(await demoSource.dayNote(FIRST)).toBe('')
      expect((await demoSource.calendar(MONTH, NO_FILTERS)).noted).toEqual([])
    }
  })

  it('is per day, and goes back to nothing with the rest of the sample journal', async () => {
    await demoSource.saveDayNote(FIRST, '<p>One</p>')
    expect(await demoSource.dayNote(days[1] as string)).toBe('')
    resetDemoJournal()
    expect(await demoSource.dayNote(FIRST)).toBe('')
  })

  it('lists the days of a month oldest first', async () => {
    for (const d of ['2026-09-20', '2026-09-03', '2026-09-11']) await demoSource.saveDayNote(d, '<p>x</p>')
    expect((await demoSource.calendar('2026-09', NO_FILTERS)).noted).toEqual(['2026-09-03', '2026-09-11', '2026-09-20'])
  })
})
