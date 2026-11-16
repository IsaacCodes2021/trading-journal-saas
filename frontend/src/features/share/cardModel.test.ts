import { describe, expect, it } from 'vitest'
import type { TradeDetail } from '../../lib/data/model'
import { cardModel, cardNumber, notesText, type CardOptions } from './cardModel'

const MINUS = '−'

const WIN: TradeDetail = {
  id: 9001,
  number: 42,
  account: { id: 1, name: 'Funded 50K', firm: 'Apex', account_type: 'funded' },
  symbol: 'NQZ6',
  symbol_root: 'NQ',
  direction: 'long',
  entry_at: '2026-11-13T14:42:18Z',
  exit_at: '2026-11-13T14:58:04Z',
  trade_day: '2026-11-13',
  hold_seconds: 946,
  total_qty: 2,
  avg_entry: '21482.25',
  avg_exit: '21519.75',
  gross_pnl: '1500.00',
  fees: '8.40',
  net_pnl: '1491.60',
  tags: [
    { id: 1, group_id: 1, name: 'A+ setup', color: '#6EA8FE' },
    { id: 2, group_id: 3, name: 'Calm', color: '#34D399' },
  ],
  has_notes: true,
  screenshot_count: 0,
  points: '37.50',
  fill_pairs: [],
  notes_html:
    '<p>Waited for the <b>opening-range</b> high.</p><ul><li>Bought the retest</li><li>Sold into it</li></ul>',
  screenshots: [],
}

const SHORT_LOSS: TradeDetail = {
  ...WIN,
  direction: 'short',
  entry_at: '2026-11-13T15:14:02Z',
  exit_at: '2026-11-13T15:21:40Z',
  avg_entry: '21519.75',
  avg_exit: '21531.00',
  gross_pnl: '-450.00',
  net_pnl: '-458.40',
  points: '-11.25',
}

const ALL_ON: CardOptions = {
  style: 'gold',
  shot: true,
  notes: true,
  tags: true,
  account: true,
  signature: true,
  amounts: 'dollars',
}

const ctx = { username: 'Trader_Joe', timeZone: 'America/New_York' }

describe('the card', () => {
  it('lists a long as bought then sold, with prices, times in the zone, quantity and hold', () => {
    const card = cardModel(WIN, ALL_ON, ctx)
    expect(card.legs).toEqual([
      { label: 'Bought', value: '21,482.25', sub: '09:42:18 ET' },
      { label: 'Sold', value: '21,519.75', sub: '09:58:04 ET' },
      { label: 'Qty', value: '2', sub: 'contracts' },
      { label: 'Hold', value: '15m 46s', sub: '+37.50 pts' },
    ])
    expect(card.resultLabel).toBe('Net P&L')
    expect(card.result).toBe('+$1,491.60')
    expect(card.sub).toBe('Gross +$1,500.00 · Fees $8.40')
    expect(card.tone).toBe('pos')
  })

  it('lists a short in the order it happened: sold first, then bought', () => {
    const card = cardModel(SHORT_LOSS, ALL_ON, ctx)
    expect(card.legs.slice(0, 2)).toEqual([
      { label: 'Sold', value: '21,519.75', sub: '10:14:02 ET' },
      { label: 'Bought', value: '21,531.00', sub: '10:21:40 ET' },
    ])
    expect(card.tone).toBe('neg')
    expect(card.result).toBe(`${MINUS}$458.40`)
    expect(card.direction).toBe('short')
  })

  it('says "contract" for one', () => {
    expect(cardModel({ ...WIN, total_qty: 1 }, ALL_ON, ctx).legs[2]).toEqual({
      label: 'Qty',
      value: '1',
      sub: 'contract',
    })
  })

  it('hides every dollar figure, the quantity and the fees in Points only', () => {
    const card = cardModel(WIN, { ...ALL_ON, amounts: 'points' }, ctx)
    expect(card.pointsOnly).toBe(true)
    expect(card.resultLabel).toBe('Result')
    expect(card.result).toBe('+37.50 pts')
    expect(card.sub).toBe('per contract')
    expect(card.legs.map((leg) => leg.label)).toEqual(['Bought', 'Sold', 'Hold'])
    expect(card.legs[2]?.sub).toBe('') // the points are already the result
    expect(JSON.stringify(card)).not.toContain('$')
    expect(JSON.stringify(card)).not.toContain('Fees')
  })

  it('takes its colour from the figure it shows', () => {
    // A loss in dollars can be a gain in points (fees): the foil follows what is written on the card.
    const feesOnly = { ...WIN, gross_pnl: '2.00', net_pnl: '-6.40', points: '0.50' }
    expect(cardModel(feesOnly, ALL_ON, ctx).tone).toBe('neg')
    expect(cardModel(feesOnly, { ...ALL_ON, amounts: 'points' }, ctx).tone).toBe('pos')
  })

  it('treats a zero result as flat, written without a sign', () => {
    const card = cardModel({ ...WIN, gross_pnl: '8.40', net_pnl: '0.00' }, ALL_ON, ctx)
    expect(card.tone).toBe('flat')
    expect(card.result).toBe('$0.00')
  })

  it('steps a result longer than ten characters down a size', () => {
    expect(cardModel(WIN, ALL_ON, ctx).resultLong).toBe(false) // "+$1,491.60" is ten
    expect(cardModel({ ...WIN, net_pnl: '12491.60' }, ALL_ON, ctx).resultLong).toBe(true) // "+$12,491.60" is eleven
    expect(cardModel({ ...WIN, net_pnl: '-12491.60' }, ALL_ON, ctx).resultLong).toBe(true)
  })

  it('numbers the card with at least five digits, on the card and round the seal', () => {
    expect(cardNumber(1)).toBe('00001')
    expect(cardNumber(42)).toBe('00042')
    expect(cardNumber(123456)).toBe('123456')
    const card = cardModel(WIN, ALL_ON, ctx)
    expect(card.number).toBe('00042')
    expect(card.sealText).toBe('ROUNDTURN · JOURNALED TRADE · No. 00042 ·')
  })

  it('writes the day out in full, with the account name only when asked', () => {
    expect(cardModel(WIN, ALL_ON, ctx).meta).toBe('Fri, Nov 13, 2026 · Funded 50K')
    expect(cardModel(WIN, { ...ALL_ON, account: false }, ctx).meta).toBe('Fri, Nov 13, 2026')
  })

  it('titles the holographic card differently from the certificates', () => {
    expect(cardModel(WIN, ALL_ON, ctx).title).toBe('Certificate of Trade')
    expect(cardModel(WIN, { ...ALL_ON, style: 'paper' }, ctx).title).toBe('Certificate of Trade')
    expect(cardModel(WIN, { ...ALL_ON, style: 'holo' }, ctx).title).toBe('Journaled trade')
  })

  it('shows notes, tags and the signature only when switched on and there is something to show', () => {
    const on = cardModel(WIN, ALL_ON, ctx)
    expect(on.notes).toBe('Waited for the opening-range high. Bought the retest Sold into it')
    expect(on.tags?.map((t) => t.name)).toEqual(['A+ setup', 'Calm'])
    expect(on.signature).toBe('Trader_Joe')
    const off = cardModel(WIN, { ...ALL_ON, notes: false, tags: false, signature: false }, ctx)
    expect([off.notes, off.tags, off.signature]).toEqual([null, null, null])
    const empty = cardModel({ ...WIN, notes_html: '', tags: [] }, ALL_ON, { ...ctx, username: null })
    expect([empty.notes, empty.tags, empty.signature]).toEqual([null, null, null])
  })
})

describe('notes as plain text', () => {
  it('flattens bold, links and lists, with a space between paragraphs and items', () => {
    expect(
      notesText('<p>One <a href="https://example.com">two</a>.</p><p>Three<br>four</p><ol><li>five</li></ol>'),
    ).toBe('One two. Three four five')
  })

  it('collapses runs of white space and decodes entities', () => {
    expect(notesText('<p>  a \n\n b &amp; c  </p>')).toBe('a b & c')
  })

  it('drops anything the sanitizer would not allow', () => {
    expect(notesText('<p>Safe<script>alert(1)</script><img src=x onerror="alert(2)"></p>')).toBe('Safe')
  })

  it('is empty for an empty note', () => {
    expect(notesText('')).toBe('')
    expect(notesText('<p></p>')).toBe('')
  })
})
