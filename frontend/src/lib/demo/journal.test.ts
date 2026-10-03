import Decimal from 'decimal.js'
import { afterEach, describe, expect, it } from 'vitest'
import { ScreenshotRefused } from '../data/model'
import { SAMPLE_TAGS, SAMPLE_TRADES, fillPairsFor, notesFor, pointsFor, screenshotsFor } from './generate'
import { demoSource } from './source'
import { MAX_SCREENSHOT_BYTES, resetDemoJournal } from './journal'

afterEach(resetDemoJournal)

const image = (name = 'chart.png', type = 'image/png', size = 100) => {
  const file = new File(['x'], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

const withNotes = SAMPLE_TRADES.find((t) => t.has_notes && t.tags.length === 0) ?? (SAMPLE_TRADES[0] as never)
const plain = SAMPLE_TRADES.find((t) => !t.has_notes && t.screenshot_count === 0) ?? (SAMPLE_TRADES[1] as never)

describe('fill pairs', () => {
  it('add up to the trade: quantities, gross result, and the first entry and last exit', () => {
    for (const trade of SAMPLE_TRADES) {
      const pairs = fillPairsFor(trade)
      expect(pairs.length).toBeGreaterThan(0)
      expect(pairs.reduce((n, p) => n + p.qty, 0)).toBe(trade.total_qty)
      expect(pairs.reduce((sum, p) => sum.plus(p.pnl), new Decimal(0)).toFixed(2)).toBe(trade.gross_pnl)
      const long = trade.direction === 'long'
      const entries = pairs.map((p) => (long ? p.bought_at : p.sold_at)).sort()
      const exits = pairs.map((p) => (long ? p.sold_at : p.bought_at)).sort()
      expect(entries[0]).toBe(trade.entry_at)
      expect(exits[exits.length - 1]).toBe(trade.exit_at)
      for (const p of pairs) expect(p.qty).toBeGreaterThan(0)
    }
  })

  it('are the same every time, with ids of their own', () => {
    const trade = SAMPLE_TRADES[10] as (typeof SAMPLE_TRADES)[number]
    expect(fillPairsFor(trade)).toEqual(fillPairsFor(trade))
    const ids = SAMPLE_TRADES.flatMap((t) => fillPairsFor(t).map((p) => p.id))
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('buys at the entry and sells at the exit for a long, the reverse for a short', () => {
    const long = SAMPLE_TRADES.find((t) => t.direction === 'long') as (typeof SAMPLE_TRADES)[number]
    const short = SAMPLE_TRADES.find((t) => t.direction === 'short') as (typeof SAMPLE_TRADES)[number]
    expect(fillPairsFor(long)[0]).toMatchObject({ buy_price: long.avg_entry, sell_price: long.avg_exit })
    expect(fillPairsFor(short)[0]).toMatchObject({ buy_price: short.avg_exit, sell_price: short.avg_entry })
  })

  it('give points that agree with the sign of the result (fees aside)', () => {
    for (const trade of SAMPLE_TRADES) {
      expect(new Decimal(pointsFor(trade)).isPositive()).toBe(new Decimal(trade.gross_pnl).isPositive())
    }
  })
})

describe('sample notes and screenshots', () => {
  it('has a note for each trade that is marked as having one, and a picture list of the right size', () => {
    expect(notesFor(withNotes)).toMatch(/^<p>/)
    for (const trade of SAMPLE_TRADES.slice(0, 20)) {
      expect(screenshotsFor(trade)).toHaveLength(trade.screenshot_count)
    }
  })
})

describe('the sample journal', () => {
  it('reads a trade with its note, pairs, points and screenshots', async () => {
    const detail = await demoSource.trade(withNotes.id)
    expect(detail?.notes_html).toBe(notesFor(withNotes))
    expect(detail?.fill_pairs.length).toBeGreaterThan(0)
    expect(detail?.points).toBe(pointsFor(withNotes))
    expect(await demoSource.trade(999_999)).toBeNull()
  })

  it('keeps a saved note, and the trade log then knows the trade has one', async () => {
    await demoSource.saveNotes(plain.id, '<p>hello</p>')
    expect((await demoSource.trade(plain.id))?.notes_html).toBe('<p>hello</p>')
    expect((await demoSource.trade(plain.id))?.has_notes).toBe(true)
    await demoSource.saveNotes(plain.id, '<p></p>')
    expect((await demoSource.trade(plain.id))?.has_notes).toBe(false)
  })

  it('tags and untags a trade, in the log too, and finds the trade by its new tag', async () => {
    await demoSource.setTradeTag(plain.id, SAMPLE_TAGS.pullback.id, true)
    expect((await demoSource.trade(plain.id))?.tags.map((t) => t.name)).toContain('Pullback')
    const page = await demoSource.trades({ preset: 'all', sort: 'date', page: 1, per_page: 50, q: 'pullback' })
    expect(page.items.map((t) => t.id)).toContain(plain.id)
    await demoSource.setTradeTag(plain.id, SAMPLE_TAGS.pullback.id, false)
    expect((await demoSource.trade(plain.id))?.tags.map((t) => t.name)).not.toContain('Pullback')
  })

  it('makes a tag once per group and name, ignoring case and extra spaces', async () => {
    const first = await demoSource.createTag(1, '  Break   and retest ')
    expect(first.name).toBe('Break and retest')
    const again = await demoSource.createTag(1, 'break and RETEST')
    expect(again.id).toBe(first.id)
    const other = await demoSource.createTag(2, 'Break and retest')
    expect(other.id).not.toBe(first.id)
    expect((await demoSource.tagCatalog()).tags.filter((t) => t.name === 'Break and retest')).toHaveLength(2)
  })

  it('refuses an empty name, a long one and an unknown group', async () => {
    await expect(demoSource.createTag(1, '   ')).rejects.toThrow('Give the tag a name')
    await expect(demoSource.createTag(1, 'x'.repeat(41))).rejects.toThrow('at most 40')
    await expect(demoSource.createTag(99, 'Fine')).rejects.toThrow('Choose a group')
  })

  it('adds and deletes screenshots, and refuses the wrong kind of file, all or nothing', async () => {
    const added = await demoSource.addScreenshots(plain.id, [image('a.png'), image('b.webp', 'image/webp')])
    expect(added.map((s) => s.original_name)).toEqual(['a.png', 'b.webp'])
    expect(added.every((s) => s.src?.startsWith('blob:'))).toBe(true)
    expect((await demoSource.trade(plain.id))?.screenshots).toHaveLength(2)

    await expect(demoSource.addScreenshots(plain.id, [image('c.png'), image('d.txt', 'text/plain')])).rejects.toThrow(
      ScreenshotRefused,
    )
    await expect(
      demoSource.addScreenshots(plain.id, [image('big.png', 'image/png', MAX_SCREENSHOT_BYTES + 1)]),
    ).rejects.toThrow('larger than 10 MB')
    expect((await demoSource.trade(plain.id))?.screenshots).toHaveLength(2) // the good file beside the bad one was not kept

    await demoSource.deleteScreenshot(plain.id, added[0]?.id ?? 0)
    expect((await demoSource.trade(plain.id))?.screenshots.map((s) => s.original_name)).toEqual(['b.webp'])
  })

  it('refuses a write to a trade that does not exist', async () => {
    await expect(demoSource.saveNotes(999_999, '<p>x</p>')).rejects.toThrow('does not exist')
  })

  it('finds the trades either side in the list the person came from', async () => {
    const query = { preset: 'all', sort: 'date', page: 1, per_page: 12 } as const
    const ordered = (await demoSource.trades({ ...query, per_page: 50 })).items.map((t) => t.id)
    expect(await demoSource.neighbors(ordered[5] ?? 0, query)).toEqual({ previous: ordered[4], next: ordered[6] })
    expect(await demoSource.neighbors(ordered[0] ?? 0, query)).toEqual({ previous: null, next: ordered[1] })
    const filtered = { ...query, symbol_roots: ['NOPE'] }
    expect(await demoSource.neighbors(ordered[0] ?? 0, filtered)).toEqual({ previous: null, next: null })
  })
})
