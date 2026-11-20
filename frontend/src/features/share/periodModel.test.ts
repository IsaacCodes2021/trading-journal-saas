import { describe, expect, it } from 'vitest'
import type { CardTrade, PeriodCard, SummaryStats } from '../../lib/data/model'
import { periodCardModel, type PeriodOptions } from './periodModel'

const MINUS = '−'

const SUMMARY: SummaryStats = {
  trade_count: 6,
  account_count: 2,
  win_count: 4,
  loss_count: 2,
  breakeven_count: 0,
  gross_pnl: '1831.00',
  fees: '25.62',
  net_pnl: '1805.38',
  total_won: '2300.00',
  total_lost: '-494.62',
  win_rate: 4 / 6,
  profit_factor: null,
  avg_win: null,
  avg_loss: null,
  win_loss_ratio: null,
  expectancy: null,
  largest_win: '1215.00',
  largest_loss: '-434.40',
  trading_days: 1,
  winning_days: 1,
  losing_days: 0,
  max_drawdown: '434.40',
}

const trade = (id: number, change: Partial<CardTrade>): CardTrade => ({
  id,
  symbol: 'NQZ6',
  direction: 'long',
  trade_day: '2026-11-13',
  entry_at: '2026-11-13T14:42:18Z', // 09:42 in New York
  total_qty: 2,
  net_pnl: '0.00',
  points: '0',
  ...change,
})

const DAY: PeriodCard = {
  start: '2026-11-13',
  end: '2026-11-13',
  number: 57,
  summary: SUMMARY,
  contracts: 14,
  net_points: '29.85',
  accounts: ['Apex 150K', 'Lucid 100K'],
  best: trade(1, { symbol: 'GCZ6', direction: 'short', net_pnl: '1215.00', points: '6.1' }),
  worst: trade(2, { net_pnl: '-434.40', points: '-10.75', entry_at: '2026-11-13T16:02:00Z' }),
  best_points: trade(3, { net_pnl: '580.00', points: '14.5', total_qty: 1 }),
  worst_points: trade(2, { net_pnl: '-434.40', points: '-10.75', entry_at: '2026-11-13T16:02:00Z' }),
}

const WEEK: PeriodCard = {
  ...DAY,
  start: '2026-11-29',
  end: '2026-12-05',
  number: 12,
  summary: { ...SUMMARY, trading_days: 4, winning_days: 3, losing_days: 1 },
  best: trade(1, { trade_day: '2026-12-02', entry_at: '2026-12-02T14:42:18Z', net_pnl: '1215.00', points: '6.1' }),
}

const ALL_ON: PeriodOptions = { style: 'gold', notes: true, account: true, signature: true, amounts: 'dollars' }
const ctx = { username: 'Trader_Joe', timeZone: 'America/New_York' }
const day = { kind: 'day', start: '2026-11-13' } as const
const week = { kind: 'week', start: '2026-11-29' } as const

describe('a day’s card', () => {
  it('heads the card with the day and the accounts, with no side, and the net result in dollars', () => {
    const card = periodCardModel(DAY, day, ALL_ON, ctx)
    expect(card.kind).toBe('day')
    expect(card.heading).toBe('Fri, Nov 13, 2026')
    expect(card.direction).toBeNull()
    expect(card.meta).toBe('2 accounts')
    expect([card.resultLabel, card.result, card.sub]).toEqual([
      'Net P&L',
      '+$1,805.38',
      'Gross +$1,831.00 · Fees $25.62',
    ])
    expect(card.tone).toBe('pos')
  })

  it('lists the trades, the contracts, the net points and the win rate', () => {
    expect(periodCardModel(DAY, day, ALL_ON, ctx).legs).toEqual([
      { label: 'Trades', value: '6', sub: '4 W · 2 L' },
      { label: 'Contracts', value: '14', sub: '' },
      { label: 'Net points', value: '+29.85', sub: 'per contract' },
      { label: 'Win rate', value: '67%', sub: '' },
    ])
  })

  it('names the best and the worst trade by money, with their time, points and size', () => {
    expect(periodCardModel(DAY, day, ALL_ON, ctx).extremes).toEqual([
      {
        label: 'Best trade',
        symbol: 'GCZ6',
        direction: 'short',
        when: '09:42 ET',
        value: '+$1,215.00',
        sub: '+6.10 pts · 2 contracts',
        tone: 'pos',
      },
      {
        label: 'Worst trade',
        symbol: 'NQZ6',
        direction: 'long',
        when: '11:02 ET',
        value: `${MINUS}$434.40`,
        sub: `${MINUS}10.75 pts · 2 contracts`,
        tone: 'neg',
      },
    ])
  })

  it('in Points only, makes the net points the result, ranks the trades by points and keeps the contracts', () => {
    const card = periodCardModel(DAY, day, { ...ALL_ON, amounts: 'points' }, ctx)
    expect([card.resultLabel, card.result, card.sub]).toEqual(['Net points', '+29.85 pts', 'per contract, added up'])
    expect(card.legs.map((leg) => leg.label)).toEqual(['Trades', 'Contracts', 'Win rate'])
    expect(card.extremes?.map((x) => [x.label, x.value, x.sub])).toEqual([
      ['Best trade', '+14.50 pts', '1 contract'],
      ['Worst trade', `${MINUS}10.75 pts`, '2 contracts'],
    ])
    expect(JSON.stringify(card)).not.toContain('$')
    expect(JSON.stringify(card)).not.toContain('Fees')
  })

  it('takes its colour from the figure it shows', () => {
    // Down in money after fees, up in points: the foil follows what the card says.
    const feesDay = { ...DAY, summary: { ...SUMMARY, net_pnl: '-6.40' }, net_points: '0.5' }
    expect(periodCardModel(feesDay, day, ALL_ON, ctx).tone).toBe('neg')
    expect(periodCardModel(feesDay, day, { ...ALL_ON, amounts: 'points' }, ctx).tone).toBe('pos')
  })

  it('has one line for a day with a single trade', () => {
    const single = { ...DAY, summary: { ...SUMMARY, trade_count: 1 }, worst: DAY.best, worst_points: DAY.best }
    const lines = periodCardModel(single, day, ALL_ON, ctx).extremes
    expect(lines?.map((x) => [x.label, x.symbol])).toEqual([['Only trade', 'GCZ6']])
  })

  it('numbers and titles the card as a trading day', () => {
    const card = periodCardModel(DAY, day, ALL_ON, ctx)
    expect(card.number).toBe('00057')
    expect(card.sealText).toBe('ROUNDTURN · TRADING DAY · No. 00057 ·')
    expect(card.title).toBe('Certificate of the Day')
    expect(periodCardModel(DAY, day, { ...ALL_ON, style: 'holo' }, ctx).title).toBe('Journaled day')
  })

  it('shows the day’s note, the account name and the signature only when switched on', () => {
    const one = { ...DAY, accounts: ['Apex 150K'] }
    const on = periodCardModel(one, day, ALL_ON, ctx, '<p>Waited for the <b>open</b>.</p><p>Stopped at two losses.</p>')
    expect([on.notes, on.meta, on.signature]).toEqual([
      'Waited for the open. Stopped at two losses.',
      'Apex 150K',
      'Trader_Joe',
    ])
    const off = periodCardModel(
      one,
      day,
      { ...ALL_ON, notes: false, account: false, signature: false },
      ctx,
      '<p>x</p>',
    )
    expect([off.notes, off.meta, off.signature]).toEqual([null, '', null])
    expect(periodCardModel(one, day, ALL_ON, { ...ctx, username: null }).signature).toBeNull()
    expect(periodCardModel(one, day, ALL_ON, ctx, '').notes).toBeNull()
  })

  it('has no screenshot and no tags', () => {
    const card = periodCardModel(DAY, day, ALL_ON, ctx)
    expect([card.shot, card.tags]).toEqual([false, null])
  })
})

describe('a week’s card', () => {
  it('heads the card with the week, Sunday to Saturday, and titles and numbers it as a week', () => {
    const card = periodCardModel(WEEK, week, ALL_ON, ctx)
    expect(card.kind).toBe('week')
    expect(card.heading).toBe('Nov 29 – Dec 5, 2026')
    expect(card.title).toBe('Certificate of the Week')
    expect(periodCardModel(WEEK, week, { ...ALL_ON, style: 'holo' }, ctx).title).toBe('Journaled week')
    expect(card.sealText).toBe('ROUNDTURN · TRADING WEEK · No. 00012 ·')
  })

  it('starts with the days it traded, then the trades, the contracts and the net points', () => {
    expect(periodCardModel(WEEK, week, ALL_ON, ctx).legs).toEqual([
      { label: 'Days', value: '4', sub: '3 up · 1 down' },
      { label: 'Trades', value: '6', sub: '4 W · 2 L' },
      { label: 'Contracts', value: '14', sub: '' },
      { label: 'Net points', value: '+29.85', sub: 'per contract' },
    ])
    const points = periodCardModel(WEEK, week, { ...ALL_ON, amounts: 'points' }, ctx)
    expect(points.legs.map((leg) => leg.label)).toEqual(['Days', 'Trades', 'Contracts', 'Win rate'])
  })

  it('names the weekday of the best and the worst trade', () => {
    const lines = periodCardModel(WEEK, week, ALL_ON, ctx).extremes
    expect(lines?.map((x) => x.when)).toEqual(['Wed 09:42 ET', 'Fri 11:02 ET'])
  })

  it('never carries a note, even one passed in', () => {
    expect(periodCardModel(WEEK, week, ALL_ON, ctx, '<p>Not for a week</p>').notes).toBeNull()
  })
})
