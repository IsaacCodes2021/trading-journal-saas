import Decimal from 'decimal.js'
import { describe, expect, it } from 'vitest'
import type { TradeRow } from '../data/model'
import { SAMPLE_ACCOUNTS, SAMPLE_TRADES, generateTrades, sampleDays } from './generate'
import { demoSource } from './source'
import {
  accountSummaries,
  applyFilters,
  dailyResults,
  equityByDay,
  equityByTrade,
  performanceScore,
  presetStart,
  summarize,
} from './stats'

const account = SAMPLE_ACCOUNTS[0] as (typeof SAMPLE_ACCOUNTS)[number]

function trade(id: number, net: string, day: string, over: Partial<TradeRow> = {}): TradeRow {
  return {
    id,
    account,
    symbol: 'NQZ6',
    symbol_root: 'NQ',
    direction: 'long',
    entry_at: `${day}T14:00:00Z`,
    exit_at: `${day}T14:${String(10 + id).padStart(2, '0')}:00Z`,
    trade_day: day,
    hold_seconds: 600,
    total_qty: 1,
    avg_entry: '20000.00',
    avg_exit: '20010.00',
    gross_pnl: net,
    fees: '0.00',
    net_pnl: net,
    tags: [],
    has_notes: false,
    screenshot_count: 0,
    ...over,
  }
}

// Hand-worked: wins 100 + 30 + 20 = 150, losses 50 + 80 = 130, net 20; equity 100, 50, 80, 0, 20, so the deepest
// drop from the running peak (100) is to 0: a drawdown of 100.
const MIXED = [
  trade(1, '100.00', '2026-09-01'),
  trade(2, '-50.00', '2026-09-01'),
  trade(3, '30.00', '2026-09-02'),
  trade(4, '-80.00', '2026-09-02'),
  trade(5, '20.00', '2026-09-03'),
]

describe('summarize', () => {
  it('matches a hand-worked set', () => {
    const s = summarize(MIXED)
    expect(s).toMatchObject({
      trade_count: 5,
      win_count: 3,
      loss_count: 2,
      breakeven_count: 0,
      net_pnl: '20.00',
      total_won: '150.00',
      total_lost: '-130.00',
      avg_win: '50.00',
      avg_loss: '-65.00',
      expectancy: '4.00',
      largest_win: '100.00',
      largest_loss: '-80.00',
      max_drawdown: '100.00',
      trading_days: 3,
      winning_days: 2,
      losing_days: 1,
    })
    expect(s.win_rate).toBeCloseTo(0.6)
    expect(s.profit_factor).toBeCloseTo(150 / 130)
    expect(s.win_loss_ratio).toBeCloseTo(50 / 65)
  })

  it('counts a zero trade as breakeven, not a win or a loss', () => {
    const s = summarize([trade(1, '10.00', '2026-09-01'), trade(2, '0.00', '2026-09-01')])
    expect(s).toMatchObject({ win_count: 1, loss_count: 0, breakeven_count: 1 })
    expect(s.win_rate).toBe(1)
  })

  it('says nothing is defined, rather than inventing a number, for an empty set or one without losses', () => {
    const empty = summarize([])
    expect(empty).toMatchObject({ trade_count: 0, net_pnl: '0.00', max_drawdown: '0.00' })
    expect(empty.win_rate).toBeNull()
    expect(empty.expectancy).toBeNull()
    expect(empty.largest_win).toBeNull()
    const onlyWins = summarize([trade(1, '10.00', '2026-09-01')])
    expect(onlyWins.profit_factor).toBeNull()
    expect(onlyWins.avg_loss).toBeNull()
  })

  it('adds exactly, with no floating-point drift', () => {
    const tenths = Array.from({ length: 10 }, (_, i) => trade(i + 1, '0.10', '2026-09-01'))
    expect(summarize(tenths).net_pnl).toBe('1.00')
  })
})

describe('equity curves and daily results', () => {
  it('starts the curve at the first result and tracks the drop from the peak', () => {
    const curve = equityByTrade(MIXED)
    expect(curve.map((p) => p.equity)).toEqual(['100.00', '50.00', '80.00', '0.00', '20.00'])
    expect(curve.map((p) => p.drawdown)).toEqual(['0.00', '-50.00', '-20.00', '-100.00', '-80.00'])
  })

  it('orders the trade curve by exit time whatever order the rows arrive in', () => {
    const reversed = [...MIXED].reverse()
    expect(equityByTrade(reversed).map((p) => p.equity)).toEqual(equityByTrade(MIXED).map((p) => p.equity))
  })

  it('totals each trading day, oldest first, and builds the day curve from them', () => {
    const days = dailyResults([...MIXED].reverse())
    expect(days).toEqual([
      { day: '2026-09-01', net_pnl: '50.00', trade_count: 2 },
      { day: '2026-09-02', net_pnl: '-50.00', trade_count: 2 },
      { day: '2026-09-03', net_pnl: '20.00', trade_count: 1 },
    ])
    expect(equityByDay(days).map((p) => p.equity)).toEqual(['50.00', '0.00', '20.00'])
  })
})

describe('performanceScore', () => {
  it('has no score without trades', () => {
    expect(performanceScore(summarize([]), [])).toBeNull()
  })

  it('has the six axes, each 0 to 100, each with its rule', () => {
    const score = performanceScore(summarize(MIXED), dailyResults(MIXED))
    expect(score?.axes.map((a) => a.name)).toEqual([
      'Win rate',
      'Profit factor',
      'Avg win/loss',
      'Consistency',
      'Max drawdown',
      'Recovery factor',
    ])
    for (const axis of score?.axes ?? []) {
      expect(axis.score).toBeGreaterThanOrEqual(0)
      expect(axis.score).toBeLessThanOrEqual(100)
      expect(axis.rule.length).toBeGreaterThan(10)
    }
    expect(score?.overall).toBeGreaterThan(0)
    expect(score?.overall).toBeLessThan(100)
  })

  it('scores win rate on the 0 to 60% scale', () => {
    const score = performanceScore(summarize(MIXED), dailyResults(MIXED))
    expect(score?.axes[0]?.score).toBe(100) // 60% win rate is the ceiling
  })

  it('gives a perfect profit factor to a run with no losers', () => {
    const wins = [trade(1, '10.00', '2026-09-01'), trade(2, '20.00', '2026-09-02')]
    const score = performanceScore(summarize(wins), dailyResults(wins))
    expect(score?.axes[1]?.score).toBe(100)
    expect(score?.axes[2]?.score).toBe(100)
  })
})

describe('filters', () => {
  it('counts preset ranges back from the last trading day', () => {
    expect(presetStart('7d', '2026-10-02')).toBe('2026-09-26')
    expect(presetStart('30d', '2026-10-02')).toBe('2026-09-03')
    expect(presetStart('ytd', '2026-10-02')).toBe('2026-01-01')
    expect(presetStart('all', '2026-10-02')).toBeNull()
  })

  it('narrows by account type, firm, symbol and side together', () => {
    const rows = [
      trade(1, '1.00', '2026-09-01'),
      trade(2, '1.00', '2026-09-01', { direction: 'short' }),
      trade(3, '1.00', '2026-09-01', { symbol_root: 'ES' }),
      trade(4, '1.00', '2026-09-01', { account: { ...account, id: 9, firm: 'Lucid', account_type: 'evaluation' } }),
    ]
    const ids = (filters: Parameters<typeof applyFilters>[1]) =>
      applyFilters(rows, filters, '2026-09-01').map((t) => t.id)
    expect(ids({ preset: 'all' })).toEqual([1, 2, 3, 4])
    expect(ids({ preset: 'all', direction: 'short' })).toEqual([2])
    expect(ids({ preset: 'all', symbol_roots: ['ES'] })).toEqual([3])
    expect(ids({ preset: 'all', firms: ['Lucid'] })).toEqual([4])
    expect(ids({ preset: 'all', account_type: 'evaluation' })).toEqual([4])
    expect(ids({ preset: 'all', account_type: 'funded', direction: 'long' })).toEqual([1, 3])
  })

  it('groups results by account, best first', () => {
    const other = { ...account, id: 9, name: 'Other' }
    const rows = [trade(1, '10.00', '2026-09-01'), trade(2, '40.00', '2026-09-01', { account: other })]
    expect(accountSummaries(rows).map((a) => [a.account.name, a.net_pnl, a.trade_count])).toEqual([
      ['Other', '40.00', 1],
      [account.name, '10.00', 1],
    ])
  })
})

describe('the sample data', () => {
  it('is the same every time: 132 trades over 22 weekdays', () => {
    expect(SAMPLE_TRADES).toHaveLength(132)
    expect(sampleDays()).toHaveLength(22)
    expect(generateTrades()).toEqual(SAMPLE_TRADES)
    expect(generateTrades(7)).not.toEqual(SAMPLE_TRADES)
  })

  it('numbers trades in exit order and keeps every figure an exact two-decimal string', () => {
    const exits = SAMPLE_TRADES.map((t) => t.exit_at)
    expect([...exits].sort()).toEqual(exits)
    expect(SAMPLE_TRADES.map((t) => t.id)).toEqual(SAMPLE_TRADES.map((_, i) => i + 1))
    for (const t of SAMPLE_TRADES) {
      expect(t.net_pnl).toMatch(/^-?\d+\.\d{2}$/)
      expect(new Decimal(t.gross_pnl).minus(t.fees).toFixed(2)).toBe(t.net_pnl)
      expect(t.exit_at > t.entry_at).toBe(true)
      expect(t.hold_seconds).toBeGreaterThan(0)
    }
  })

  it('answers the dashboard consistently: the parts add up to the whole', async () => {
    const data = await demoSource.dashboard({ preset: 'all' })
    const total = data.daily.reduce((sum, d) => sum.plus(d.net_pnl), new Decimal(0))
    expect(total.toFixed(2)).toBe(data.summary.net_pnl)
    expect(data.accounts.reduce((n, a) => n + a.trade_count, 0)).toBe(data.summary.trade_count)
    expect(data.equity_by_trade.at(-1)?.equity).toBe(data.summary.net_pnl)
    expect(data.equity_by_day.at(-1)?.equity).toBe(data.summary.net_pnl)
    expect(data.range).toEqual({ start: '2026-09-03', end: '2026-10-02' })
  })

  it('narrows the dashboard with the filters', async () => {
    const all = await demoSource.dashboard({ preset: 'all' })
    const week = await demoSource.dashboard({ preset: '7d' })
    const nq = await demoSource.dashboard({ preset: 'all', symbol_roots: ['NQ'] })
    expect(week.summary.trade_count).toBeLessThan(all.summary.trade_count)
    expect(week.summary.trading_days).toBe(5)
    expect(nq.summary.trade_count).toBeLessThan(all.summary.trade_count)
    expect(nq.summary.trade_count).toBeGreaterThan(0)
  })

  it('pages, sorts and searches the trade log', async () => {
    const base = { preset: 'all', sort: '-date', page: 1, per_page: 12 } as const
    const first = await demoSource.trades(base)
    expect(first.total).toBe(132)
    expect(first.items).toHaveLength(12)
    const dates = first.items.map((t) => t.entry_at)
    expect([...dates].sort().reverse()).toEqual(dates)

    const last = await demoSource.trades({ ...base, page: 11 })
    expect(last.items).toHaveLength(12)
    const beyond = await demoSource.trades({ ...base, page: 12 })
    expect(beyond.items).toHaveLength(0)

    const byPnl = await demoSource.trades({ ...base, sort: 'net_pnl' })
    const pnls = byPnl.items.map((t) => new Decimal(t.net_pnl).toNumber())
    expect([...pnls].sort((a, b) => a - b)).toEqual(pnls)

    const es = await demoSource.trades({ ...base, q: ' es ' })
    expect(es.total).toBeGreaterThan(0)
    expect(es.total).toBeLessThan(132)
    expect(es.items.every((t) => /es/i.test(`${t.symbol}${t.account.name}${t.tags.map((x) => x.name).join('')}`))).toBe(
      true,
    )
    // The page total follows every filter, not just the rows on screen: the same search at 50 a page adds up to it.
    const wide = await demoSource.trades({ ...base, per_page: 50, q: 'es' })
    expect(wide.total).toBeLessThanOrEqual(50)
    expect(wide.items.reduce((sum, t) => sum.plus(t.net_pnl), new Decimal(0)).toFixed(2)).toBe(wide.net_pnl)
    expect(wide.net_pnl).toBe(es.net_pnl)
  })

  it('offers only the firms and symbols that exist', async () => {
    expect(await demoSource.filterOptions()).toEqual({
      firms: ['Apex', 'Lucid'],
      symbol_roots: ['CL', 'ES', 'GC', 'MNQ', 'NQ'],
    })
  })
})
