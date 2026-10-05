import { describe, expect, it } from 'vitest'
import type { ReportBy, ReportData, TradeRow } from '../data/model'
import { SAMPLE_ACCOUNTS, SAMPLE_TRADES } from './generate'
import { report, SAMPLE_TIMEZONE } from './reports'
import { demoSource } from './source'

const [first, second] = SAMPLE_ACCOUNTS as [(typeof SAMPLE_ACCOUNTS)[number], (typeof SAMPLE_ACCOUNTS)[number]]

function trade(id: number, net: string, over: Partial<TradeRow> = {}): TradeRow {
  const day = over.trade_day ?? '2026-09-07' // a Monday
  return {
    id,
    account: first,
    symbol: 'NQZ6',
    symbol_root: 'NQ',
    direction: 'long',
    entry_at: `${day}T14:00:00Z`,
    exit_at: `${day}T14:01:00Z`,
    trade_day: day,
    hold_seconds: 60,
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

const NONE = new Map<number, string>()
const run = (by: ReportBy, trades: TradeRow[], categories = NONE): ReportData => report(by, trades, categories)
const labels = (data: ReportData) => data.groups.map((g) => g.label)
const nets = (data: ReportData) => data.groups.map((g) => g.summary.net_pnl)
const counts = (data: ReportData) => data.groups.map((g) => g.summary.trade_count)

describe('the sample reports follow the server’s rules', () => {
  it('lists contracts best result first, ties by name ignoring capitals, grouped by root', () => {
    const trades = [
      trade(1, '10.00', { symbol_root: 'NQ', symbol: 'NQZ6' }),
      trade(2, '10.00', { symbol_root: 'ES', symbol: 'ESZ6' }),
      trade(3, '10.00', { symbol_root: 'cl', symbol: 'CLZ6' }),
      trade(4, '35.00', { symbol_root: 'MNQ', symbol: 'MNQZ6' }),
      trade(5, '-5.00', { symbol_root: 'NQ', symbol: 'NQH7' }), // the next contract month: still NQ
    ]
    const data = run('symbol', trades)
    expect(labels(data)).toEqual(['MNQ', 'cl', 'ES', 'NQ'])
    expect(nets(data)).toEqual(['35.00', '10.00', '10.00', '5.00'])
    expect(data.groups.at(-1)?.summary.trade_count).toBe(2)
  })

  it('lists all seven weekdays, Monday first, by the trading day', () => {
    const trades = [
      trade(1, '10.00', { trade_day: '2026-09-07' }), // Monday
      trade(2, '-4.00', { trade_day: '2026-09-11' }), // Friday
      trade(3, '6.00', { trade_day: '2026-09-13' }), // Sunday
      // entered on Sunday evening, the start of Monday's trading day: Monday, not Sunday
      trade(4, '1.00', { trade_day: '2026-09-14', entry_at: '2026-09-13T23:00:00Z' }),
    ]
    const data = run('weekday', trades)
    expect(labels(data)).toEqual(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])
    expect(counts(data)).toEqual([2, 0, 0, 0, 1, 0, 1])
    expect(nets(data)).toEqual(['11.00', '0.00', '0.00', '0.00', '-4.00', '0.00', '6.00'])
  })

  it('lists the hours that have trades, earliest first, read in the sample timezone and padded', () => {
    expect(SAMPLE_TIMEZONE).toBe('America/New_York')
    const trades = [
      trade(1, '1.00', { entry_at: '2026-09-07T19:00:00Z' }), // 15:00 in New York (daylight time)
      trade(2, '2.00', { entry_at: '2026-09-07T13:30:00Z' }), // 09:30
      trade(3, '3.00', { entry_at: '2026-09-08T04:05:00Z' }), // 00:05, midnight is 00 and not 24
      trade(4, '4.00', { entry_at: '2026-09-07T13:59:00Z' }), // 09:59: the same hour as 09:30
    ]
    const data = run('hour', trades)
    expect(labels(data)).toEqual(['00:00', '09:00', '15:00'])
    expect(counts(data)).toEqual([1, 2, 1])
    expect(data.groups.map((g) => g.key)).toEqual(['0', '9', '15'])
  })

  it.each([
    [0, 'Under 1 minute'],
    [59, 'Under 1 minute'],
    [60, '1 to 5 minutes'],
    [299, '1 to 5 minutes'],
    [300, '5 to 15 minutes'],
    [899, '5 to 15 minutes'],
    [900, '15 to 30 minutes'],
    [1799, '15 to 30 minutes'],
    [1800, '30 to 60 minutes'],
    [3599, '30 to 60 minutes'],
    [3600, '1 to 2 hours'],
    [7199, '1 to 2 hours'],
    [7200, 'Over 2 hours'],
    [259_200, 'Over 2 hours'],
  ])('puts a hold of %i seconds in "%s": a step includes its lower edge', (seconds, step) => {
    const data = run('hold', [trade(1, '1.00', { hold_seconds: seconds })])
    expect(data.groups).toHaveLength(7) // every step is listed, with or without trades
    expect(data.groups.filter((g) => g.summary.trade_count).map((g) => g.label)).toEqual([step])
  })

  it('counts a trade under each of its tags, shows the category and groups the untagged', () => {
    const a = { id: 10, group_id: 1, name: 'A+ setup', color: '#112233' }
    const b = { id: 11, group_id: 2, name: 'FOMO', color: '#112233' }
    const c = { id: 12, group_id: 2, name: 'News', color: '#112233' }
    const trades = [
      trade(1, '30.00', { tags: [a, c] }),
      trade(2, '-10.00', { tags: [b] }),
      trade(3, '4.00', { tags: [b, c] }),
      trade(4, '-7.00'),
    ]
    const categories = new Map([
      [1, 'Setup'],
      [2, 'Mistake'],
    ])
    const data = run('tag', trades, categories)
    expect(labels(data)).toEqual(['News', 'A+ setup', 'FOMO', 'Untagged'])
    expect(nets(data)).toEqual(['34.00', '30.00', '-6.00', '-7.00'])
    expect(counts(data)).toEqual([2, 1, 2, 1])
    expect(data.groups.map((g) => g.detail)).toEqual(['Mistake', 'Setup', 'Mistake', null])
    expect(data.groups.at(-1)?.key).toBe('untagged')
    expect(data.summary.trade_count).toBe(4) // the groups add up to six, the report's total does not
    expect(data.summary.net_pnl).toBe('17.00')
  })

  it('keeps two tags with the same name in different categories apart', () => {
    const one = { id: 10, group_id: 1, name: 'Good', color: '#112233' }
    const other = { id: 11, group_id: 2, name: 'Good', color: '#112233' }
    const categories = new Map([
      [1, 'Entry'],
      [2, 'Exit'],
    ])
    const data = run('tag', [trade(1, '5.00', { tags: [one] }), trade(2, '-1.00', { tags: [other] })], categories)
    expect(data.groups.map((g) => [g.label, g.detail, g.summary.net_pnl])).toEqual([
      ['Good', 'Entry', '5.00'],
      ['Good', 'Exit', '-1.00'],
    ])
  })

  it('puts two tags of the same name and result in a fixed order, whatever order the trades came in', () => {
    const one = { id: 10, group_id: 1, name: 'Good', color: '#112233' }
    const other = { id: 11, group_id: 2, name: 'Good', color: '#112233' }
    const categories = new Map([
      [1, 'Entry'],
      [2, 'Exit'],
    ])
    const trades = [trade(1, '5.00', { tags: [other] }), trade(2, '5.00', { tags: [one] })]
    expect(run('tag', trades, categories).groups.map((g) => g.key)).toEqual(['10', '11'])
    expect(run('tag', [...trades].reverse(), categories).groups.map((g) => g.key)).toEqual(['10', '11'])
  })

  it('names the firm and kind of each account, best result first', () => {
    const trades = [trade(1, '-5.00'), trade(2, '25.00', { account: second }), trade(3, '10.00', { account: second })]
    const data = run('account', trades)
    const kind = second.account_type === 'funded' ? 'Funded' : 'Evaluation'
    expect(data.groups.map((g) => g.label)).toEqual([second.name, first.name])
    expect(data.groups[0]?.detail).toBe(`${second.firm} · ${kind}`)
    expect(data.groups.map((g) => g.key)).toEqual([String(second.id), String(first.id)])
  })

  it('answers an empty set with no groups, or all seven steps for weekday and hold', () => {
    for (const by of ['symbol', 'hour', 'tag', 'account'] as const) expect(run(by, []).groups).toEqual([])
    expect(run('weekday', []).groups).toHaveLength(7)
    expect(run('hold', []).groups).toHaveLength(7)
    expect(run('hold', []).summary.trade_count).toBe(0)
  })
})

describe('the sample source', () => {
  it('reports on every sample trade and agrees with the dashboard when nothing is filtered', async () => {
    const filters = { preset: 'all' as const }
    const dashboard = await demoSource.dashboard(filters)
    for (const by of ['symbol', 'weekday', 'hour', 'hold', 'tag', 'account'] as const) {
      const data = await demoSource.report(by, filters)
      expect(data.by).toBe(by)
      expect(data.summary).toEqual(dashboard.summary)
    }
    expect(SAMPLE_TRADES.length).toBe(dashboard.summary.trade_count)
  })

  it('names each tag’s category from the journal’s own tag groups', async () => {
    const data = await demoSource.report('tag', { preset: 'all' })
    const tagged = data.groups.filter((g) => g.key !== 'untagged')
    expect(tagged.length).toBeGreaterThan(0)
    for (const g of tagged) expect(['Setup', 'Mistake', 'Emotion'], g.label).toContain(g.detail)
  })

  it('keeps to the date range of the filters, so a week is fewer trades than everything', async () => {
    const week = await demoSource.report('symbol', { preset: '7d' })
    const everything = await demoSource.report('symbol', { preset: 'all' })
    expect(week.summary.trade_count).toBeGreaterThan(0)
    expect(week.summary.trade_count).toBeLessThan(everything.summary.trade_count)
  })

  it('applies the filters the way the dashboard does', async () => {
    const filters = { preset: 'all' as const, direction: 'short' as const }
    const dashboard = await demoSource.dashboard(filters)
    const data = await demoSource.report('account', filters)
    expect(data.summary).toEqual(dashboard.summary)
    expect(data.groups.reduce((sum, g) => sum + g.summary.trade_count, 0)).toBe(dashboard.summary.trade_count)
  })
})
